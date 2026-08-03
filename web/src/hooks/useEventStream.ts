import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { WsClient, defaultWsUrl, type WsEnvelope, type WsStatus } from '@/api/ws';
import { queryKeys } from '@/api/queryKeys';
import { useAuthStore } from '@/stores/authStore';
import type { CursorPage, EventRead, RunRead, TaskRead } from '@/api/types';

/**
 * WS 实时事件订阅 → 合并进 TanStack Query 缓存。
 *
 * 铁律（docs/TASK_BREAKDOWN.md §10.3）：实时数据只走这一条路 ——
 * WS 事件 → useEventStream → 合并进 Query 缓存 → 组件读缓存。
 * 禁止组件直接订阅 WS。
 *
 * 事件合并策略：
 * - RUN_* 事件 → 更新 runs.detail(id) 缓存中的 state/loop_index 等字段
 * - TASK_* 事件 → 更新 tasks.list 缓存
 * - 全部事件 → 追加进 events.list(run_id) 缓存（按 seq 去重、保序）
 */
export interface UseEventStreamOptions {
  runId?: string;
  enabled?: boolean;
  onEvent?: (event: EventRead) => void;
}

export function useEventStream(options: UseEventStreamOptions = {}) {
  const { runId, enabled = true, onEvent } = options;
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<WsStatus>('idle');
  const [lastSeq, setLastSeq] = useState(0);
  const clientRef = useRef<WsClient | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!enabled) return;

    const client = new WsClient({
      url: defaultWsUrl(),
      apiKey: useAuthStore.getState().apiKey,
      onStatusChange: setStatus,
    });
    clientRef.current = client;

    const unsubscribe = client.on((envelope: WsEnvelope) => {
      if (envelope.kind !== 'event' || !envelope.event_type) return;
      if (runId && envelope.run_id && envelope.run_id !== runId) return;

      const event: EventRead = {
        seq: envelope.seq,
        id: envelope.trace_id ?? `ws-${envelope.seq}`,
        run_id: envelope.run_id ?? runId ?? null,
        task_id: envelope.task_id ?? null,
        step_id: null,
        type: envelope.event_type ?? '',
        severity: (envelope.severity as EventRead['severity']) ?? 'INFO',
        payload: envelope.payload ?? {},
        trace_id: envelope.trace_id ?? null,
        prev_hash: null,
        hash: '',
        created_at: envelope.ts ?? new Date().toISOString(),
      };

      mergeEvent(queryClient, event, runId);
      setLastSeq(envelope.seq);
      onEventRef.current?.(event);
    });

    client.connect();
    return () => {
      unsubscribe();
      client.close();
      clientRef.current = null;
    };
  }, [enabled, runId, queryClient]);

  return { status, lastSeq, client: clientRef.current };
}

/** 把一条事件合并进 Query 缓存（供 WS 与 SSE 共用）。 */
export function mergeEvent(
  queryClient: ReturnType<typeof useQueryClient>,
  event: EventRead,
  runId?: string,
): void {
  // 1. 事件列表缓存：按 seq 去重并保持升序（取最近 200 条）
  const eventKey = queryKeys.events.list({ run_id: runId ?? event.run_id ?? undefined });
  queryClient.setQueryData<CursorPage<EventRead>>(eventKey, (prev) => {
    const items = prev?.items ?? [];
    if (items.some((item) => item.seq === event.seq)) return prev;
    const merged = [...items, event].sort((a, b) => a.seq - b.seq).slice(-200);
    return { items: merged, next_cursor: null, has_more: false, total: merged.length };
  });

  // 2. Run 缓存：把状态类事件反映到 runs.detail
  if (event.run_id) {
    const runKey = queryKeys.runs.detail(event.run_id);
    queryClient.setQueryData<RunRead>(runKey, (prev) => {
      if (!prev) return prev;
      const stateMap: Record<string, RunRead['state']> = {
        RUN_CREATED: 'CREATED',
        RUN_STARTED: 'RUNNING',
        RUN_PAUSED: 'PAUSED',
        RUN_RESUMED: 'RUNNING',
        RUN_CANCELLED: 'CANCELLED',
        RUN_SUCCEEDED: 'SUCCEEDED',
        RUN_FAILED: 'FAILED',
        RUN_RECOVERED: 'RECOVERING',
      };
      const nextState = stateMap[event.type];
      const patch: Partial<RunRead> = {};
      if (nextState) patch.state = nextState;
      if (event.type === 'RUN_STARTED' && !prev.started_at) patch.started_at = event.created_at;
      if (event.type === 'RUN_SUCCEEDED' || event.type === 'RUN_FAILED') patch.finished_at = event.created_at;
      return { ...prev, ...patch };
    });
  }

  // 3. Task 缓存：TASK_DONE / TASK_FAILED 等更新 tasks.list
  if (event.task_id) {
    const taskKey = queryKeys.tasks.list({});
    queryClient.setQueryData<CursorPage<TaskRead>>(taskKey, (prev) => {
      if (!prev) return prev;
      const stateMap: Record<string, TaskRead['state']> = {
        TASK_DONE: 'DONE',
        TASK_FAILED: 'FAILED',
        TASK_BLOCKED: 'BLOCKED',
        TASK_IN_REVIEW: 'IN_REVIEW',
      };
      const nextState = stateMap[event.type];
      if (!nextState) return prev;
      return {
        ...prev,
        items: prev.items.map((task) => (task.id === event.task_id ? { ...task, state: nextState } : task)),
      };
    });
  }
}
