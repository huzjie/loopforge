import type { ReactNode } from 'react';
import clsx from 'clsx';

export interface TabItem {
  key: string;
  label: ReactNode;
  badge?: number;
}

export interface TabsProps {
  items: TabItem[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}

/** 标签页。 */
export function Tabs({ items, active, onChange, className }: TabsProps) {
  return (
    <div className={clsx('flex gap-1 border-b border-border', className)}>
      {items.map((item) => (
        <button
          key={item.key}
          onClick={() => onChange(item.key)}
          className={clsx(
            '-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors',
            active === item.key
              ? 'border-brand font-medium text-brand'
              : 'border-transparent text-ink-muted hover:text-ink',
          )}
        >
          {item.label}
          {typeof item.badge === 'number' && item.badge > 0 && (
            <span className="rounded-full bg-danger/15 px-1.5 text-xs font-medium text-danger">{item.badge}</span>
          )}
        </button>
      ))}
    </div>
  );
}
