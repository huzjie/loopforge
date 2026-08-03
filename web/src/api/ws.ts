/**
 * WebSocket 客户端。
 *
 * 端点：`/api/v1/ws/events`（基址可用 VITE_WS_URL 覆盖）
 * 信封契约见 docs/ARCHITECTURE.md §10.7：
 * - 下行 kind：event / ack / ping / error / overflow
 * - 上行 kind：subscribe / unsubscribe / pong
 * - `seq` 在单个 Run 内严格单调递增且连续，断线用 `since_seq` 续传
 *
 * 特性：自动重连（指数退避 + 抖动）、断线续传、心跳 pong、订阅管理。
 */

export type WsDownKind = 'event' | 'ack' | 'ping' | 'error' | 'overflow';
export type WsUpKind = 'subscribe' | 'unsubscribe' | 'pong';

export interface WsEnvelope {
  kind: WsDownKind;
  seq: number;
  event_type?: string;
  severity?: string;
  run_id?: string;
  task_id?: string;
  trace_id?: string;
  ts?: string;
  payload?: Record<string, unknown>;
  /** ack 专用：续传起点 */
  resumed_from_seq?: number;
  /** error 专用 */
  code?: string;
  message?: string;
  /** overflow 专用 */
  dropped_count?: number;
}

export interface WsClientOptions {
  url: string;
  apiKey?: string | null;
  /** 心跳间隔（默认 30s），服务端 ping 时回 pong。 */
  heartbeatMs?: number;
  /** 重连基础退避（默认 1000ms），指数增长至 maxBackoffMs。 */
  baseBackoffMs?: number;
  maxBackoffMs?: number;
  /** 供测试注入的 WebSocket 工厂。 */
  webSocketFactory?: (url: string) => WebSocket;
  onStatusChange?: (status: WsStatus) => void;
}

export type WsStatus = 'idle' | 'connecting' | 'open' | 'closed';

type WsListener = (envelope: WsEnvelope) => void;

/** 从浏览器环境推导 WS 地址。 */
export function defaultWsUrl(): string {
  const configured = import.meta.env.VITE_WS_URL;
  if (configured) return configured;
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/api/v1/ws/events`;
}

type ResolvedOptions = WsClientOptions & {
  heartbeatMs: number;
  baseBackoffMs: number;
  maxBackoffMs: number;
  webSocketFactory: (url: string) => WebSocket;
};

export class WsClient {
  private socket: WebSocket | null = null;
  private listeners = new Set<WsListener>();
  private status: WsStatus = 'idle';
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private manualClose = false;
  private lastSeq = 0;

  readonly options: ResolvedOptions;

  constructor(options: WsClientOptions) {
    this.options = {
      ...options,
      heartbeatMs: options.heartbeatMs ?? 30_000,
      baseBackoffMs: options.baseBackoffMs ?? 1_000,
      maxBackoffMs: options.maxBackoffMs ?? 30_000,
      webSocketFactory: options.webSocketFactory ?? ((url: string) => new WebSocket(url)),
    };
  }

  getStatus(): WsStatus {
    return this.status;
  }

  getLastSeq(): number {
    return this.lastSeq;
  }

  /** 订阅下行消息。返回取消函数。 */
  on(listener: WsListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  connect(): void {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.manualClose = false;
    this.setStatus('connecting');

    const url = this.buildUrl();
    const ws = this.options.webSocketFactory(url);
    this.socket = ws;

    ws.onopen = () => {
      this.reconnectAttempt = 0;
      this.setStatus('open');
      this.sendSubscribe();
      this.startHeartbeat();
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        const envelope = JSON.parse(String(event.data)) as WsEnvelope;
        this.handleEnvelope(envelope);
      } catch {
        // 非 JSON 消息忽略（协议外数据）
      }
    };

    ws.onclose = () => {
      this.stopHeartbeat();
      this.socket = null;
      this.setStatus('closed');
      if (!this.manualClose) this.scheduleReconnect();
    };

    ws.onerror = () => {
      // onclose 一定会跟随，统一在 onclose 里重连
    };
  }

  close(): void {
    this.manualClose = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.stopHeartbeat();
    if (this.socket) {
      try {
        this.socket.close();
      } catch {
        /* ignore */
      }
      this.socket = null;
    }
    this.setStatus('closed');
  }

  /** 发送上行消息（subscribe / unsubscribe / pong）。 */
  send(kind: WsUpKind, extra?: Record<string, unknown>): void {
    const ws = this.socket;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ kind, ...extra }));
  }

  /** 订阅指定的 Run 集合（覆盖式）。 */
  subscribe(runIds: string[], types?: string[]): void {
    this.send('subscribe', { run_ids: runIds, types: types ?? [], since_seq: this.lastSeq });
  }

  unsubscribe(runIds: string[]): void {
    this.send('unsubscribe', { run_ids: runIds });
  }

  private buildUrl(): string {
    const base = this.options.url;
    const sep = base.includes('?') ? '&' : '?';
    const parts = [`${base}${sep}since_seq=${this.lastSeq}`];
    if (this.options.apiKey) parts.push(`token=${encodeURIComponent(this.options.apiKey)}`);
    return parts.join('&');
  }

  private handleEnvelope(envelope: WsEnvelope): void {
    if (envelope.kind === 'ping') {
      this.send('pong');
      return;
    }
    if (envelope.kind === 'event' && envelope.seq > this.lastSeq) {
      this.lastSeq = envelope.seq;
    }
    if (envelope.kind === 'ack' && typeof envelope.resumed_from_seq === 'number') {
      this.lastSeq = envelope.resumed_from_seq;
    }
    for (const listener of this.listeners) {
      try {
        listener(envelope);
      } catch {
        // 监听器异常不得中断其他监听器
      }
    }
  }

  private sendSubscribe(): void {
    this.send('subscribe', { run_ids: [], types: [], since_seq: this.lastSeq });
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      this.send('pong');
    }, this.options.heartbeatMs);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.manualClose) return;
    const backoff = Math.min(
      this.options.baseBackoffMs * 2 ** this.reconnectAttempt,
      this.options.maxBackoffMs,
    );
    // 抖动 ±20%，避免多个客户端同时重连
    const jitter = backoff * (0.8 + Math.random() * 0.4);
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => this.connect(), jitter);
  }

  private setStatus(status: WsStatus): void {
    this.status = status;
    this.options.onStatusChange?.(status);
  }
}
