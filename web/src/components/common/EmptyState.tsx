import type { ReactNode } from 'react';
import clsx from 'clsx';

export interface EmptyStateProps {
  title?: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
}

/** 空状态占位（带操作引导）。 */
export function EmptyState({ title = '暂无数据', description, action, icon, className }: EmptyStateProps) {
  return (
    <div className={clsx('flex flex-col items-center justify-center gap-2 py-12 text-center', className)}>
      {icon && <div className="text-ink-faint">{icon}</div>}
      <p className="text-sm font-medium text-ink-muted">{title}</p>
      {description && <p className="max-w-sm text-xs text-ink-faint">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
