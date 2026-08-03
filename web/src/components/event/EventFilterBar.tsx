import { useMemo } from 'react';
import clsx from 'clsx';
import { useUiStore } from '@/stores/uiStore';
import { useEventTypes } from '@/hooks/useEvents';
import { t } from '@/i18n';

/** 事件筛选（类型多选）。严重级与时间窗筛选在 W3 收口。 */
export function EventFilterBar() {
  const { data: types } = useEventTypes();
  const filter = useUiStore((state) => state.eventTypeFilter);
  const setFilter = useUiStore((state) => state.setEventTypeFilter);

  const typeNames = useMemo(() => (types ? Object.keys(types) : []), [types]);

  const toggle = (name: string) => {
    setFilter(filter.includes(name) ? filter.filter((x) => x !== name) : [...filter, name]);
  };

  if (typeNames.length === 0) {
    return <span className="text-xs text-ink-faint">{t('event.filter')}</span>;
  }

  return (
    <div className="flex min-w-0 items-center gap-1 overflow-x-auto">
      {typeNames.slice(0, 12).map((name) => (
        <button
          key={name}
          onClick={() => toggle(name)}
          className={clsx(
            'shrink-0 rounded-full border px-2 py-0.5 font-mono text-[10px] transition-colors',
            filter.includes(name)
              ? 'border-brand bg-brand-soft text-brand'
              : 'border-border text-ink-faint hover:text-ink',
          )}
        >
          {name}
        </button>
      ))}
      {filter.length > 0 && (
        <button className="shrink-0 text-[10px] text-ink-faint hover:text-ink" onClick={() => setFilter([])}>
          ✕
        </button>
      )}
    </div>
  );
}
