import type { HTMLAttributes, ReactNode } from 'react';
import clsx from 'clsx';

export interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  padding?: boolean;
}

/** 卡片容器：标题区 + 内容区。 */
export function Card({ title, subtitle, actions, padding = true, className, children, ...rest }: CardProps) {
  return (
    <div
      className={clsx('rounded-card border border-border bg-surface shadow-card', className)}
      {...rest}
    >
      {(title || actions) && (
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
          <div className="min-w-0">
            {title && <h3 className="truncate text-sm font-semibold text-ink">{title}</h3>}
            {subtitle && <p className="truncate text-xs text-ink-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={padding ? 'p-4' : ''}>{children}</div>
    </div>
  );
}
