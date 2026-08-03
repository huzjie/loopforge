import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { WsClient, type WsEnvelope } from './ws';

/** 可编程的 WebSocket 桩。 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  readyState = 0;
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = 3;
    this.onclose?.({});
  }

  // 测试辅助
  open(): void {
    this.readyState = 1;
    this.onopen?.({});
  }

  receive(envelope: WsEnvelope): void {
    this.onmessage?.({ data: JSON.stringify(envelope) } as MessageEvent);
  }

  error(): void {
    this.onerror?.({});
  }
}

describe('api/ws', () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('连接后发送 subscribe 订阅消息', () => {
    const client = new WsClient({
      url: 'ws://localhost/ws/events',
      webSocketFactory: (url) => new FakeWebSocket(url) as unknown as WebSocket,
    });
    client.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    expect(ws.sent.length).toBeGreaterThan(0);
    const msg = JSON.parse(ws.sent[0]) as { kind: string; since_seq: number };
    expect(msg.kind).toBe('subscribe');
    expect(msg.since_seq).toBe(0);
    client.close();
  });

  it('event 消息分发给监听器并更新 lastSeq', () => {
    const client = new WsClient({
      url: 'ws://localhost/ws/events',
      webSocketFactory: (url) => new FakeWebSocket(url) as unknown as WebSocket,
    });
    const received: WsEnvelope[] = [];
    client.on((envelope) => received.push(envelope));
    client.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    ws.receive({ kind: 'event', seq: 42, event_type: 'RUN_STARTED', payload: {} });
    expect(received).toHaveLength(1);
    expect(received[0].seq).toBe(42);
    expect(client.getLastSeq()).toBe(42);
    client.close();
  });

  it('ping 消息自动回 pong', () => {
    const client = new WsClient({
      url: 'ws://localhost/ws/events',
      webSocketFactory: (url) => new FakeWebSocket(url) as unknown as WebSocket,
    });
    client.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.receive({ kind: 'ping', seq: 0 });
    const last = JSON.parse(ws.sent[ws.sent.length - 1]) as { kind: string };
    expect(last.kind).toBe('pong');
    client.close();
  });

  it('断线后自动重连（指数退避 + 携带 since_seq 续传）', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5);
    const client = new WsClient({
      url: 'ws://localhost/ws/events',
      baseBackoffMs: 1000,
      webSocketFactory: (url) => new FakeWebSocket(url) as unknown as WebSocket,
    });
    client.connect();
    const first = FakeWebSocket.instances[0];
    first.open();
    first.receive({ kind: 'event', seq: 7, event_type: 'TASK_DONE', payload: {} });
    expect(client.getLastSeq()).toBe(7);

    // 触发断线
    first.close();
    expect(FakeWebSocket.instances).toHaveLength(1);

    // 推进退避定时器（1000 * 2^0 * 1.0 ≈ 1000ms）
    vi.advanceTimersByTime(1100);
    expect(FakeWebSocket.instances).toHaveLength(2);

    const second = FakeWebSocket.instances[1];
    expect(second.url).toContain('since_seq=7');
    second.open();
    const subscribe = JSON.parse(second.sent[0]) as { since_seq: number };
    expect(subscribe.since_seq).toBe(7);
    client.close();
  });

  it('手动 close 后不再重连', () => {
    const client = new WsClient({
      url: 'ws://localhost/ws/events',
      webSocketFactory: (url) => new FakeWebSocket(url) as unknown as WebSocket,
    });
    client.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    client.close();
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('订阅/取消订阅发送对应上行消息', () => {
    const client = new WsClient({
      url: 'ws://localhost/ws/events',
      webSocketFactory: (url) => new FakeWebSocket(url) as unknown as WebSocket,
    });
    client.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    client.subscribe(['run-1'], ['RUN_*']);
    client.unsubscribe(['run-1']);
    const kinds = ws.sent.map((s) => (JSON.parse(s) as { kind: string }).kind);
    expect(kinds).toContain('subscribe');
    expect(kinds).toContain('unsubscribe');
    client.close();
  });
});
