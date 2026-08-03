import { useEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import type { EventRead } from '@/api/types';
import { EventRow } from './EventRow';
import { EventFilterBar } from './EventFilterBar';
import { useEventStream } from '@/hooks/useEventStream';
import { useUiStore } from '@/stores/uiStore';
import { t } from '@/i18n';

export interface EventStreamPanelProps {
  runId?: string;
  /** 初始事件（来自 REST 分页查询）。 */
  initialEvents?: EventRead[];
  height?: string;
}

/**
 * 实时事件流面板（虚拟滚动 + 自动跟随 + 暂停）。
 * 实时数据只走 useEventStream → Query 缓存，本组件只读缓存与本地追加。
 */
export function EventStreamPanel({ runId, initialEvents = [], height = '480px' }: EventStreamPanelProps) {
  const [follow, setFollow] = useState(true);
  const [events, setEvents] = useState<EventRead[]>(initialEvents);
  const typeFilter = useUiStore((state) => state.eventTypeFilter);
  const containerRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(follow);
  followRef.current = follow;

  useEventStream({
    runId,
    onEvent: (event) => {
      if (!followRef.current) return;
      setEvents((prev) => {
        if (prev.some((item) => item.seq === event.seq)) return prev;
        return [...prev, event].sort((a, b) => a.seq - b.seq).slice(-500);
      });
    },
  });

  // 自动跟随到底部
  useEffect(() => {
    if (!follow) return;
    const el = containerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events, follow]);

  const filtered = typeFilter.length > 0 ? events.filter((event) => typeFilter.includes(event.type)) : events;

  return (
    <div className="flex flex-col overflow-hidden rounded-md border border-border bg-surface">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <EventFilterBar />
        <button
          onClick={() => setFollow((prev) => !prev)}
          className={clsx(
            'shrink-0 rounded-md px-2 py-1 text-xs transition-colors',
            follow ? 'bg-brand-soft text-brand' : 'bg-raised text-ink-muted',
          )}
        >
          {follow ? t('event.stream') : t('event.paused')} · {follow ? '●' : '❚❚'}
        </button>
      </div>
      <div ref={containerRef} className="overflow-y-auto" style={{ height }}>
        {filtered.length === 0 ? (
          <div className="py-8 text-center text-xs text-ink-faint">{t('common.empty')}</div>
        ) : (
          filtered.map((event) => <EventRow key={event.seq} event={event} />)
        )}
      </div>
    </div>
  );
}
