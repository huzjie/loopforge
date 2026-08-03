import type { ReactNode } from 'react';
import clsx from 'clsx';

export type BadgeTone = 'ok' | 'warn' | 'danger' | 'info' | 'muted';

export interface BadgeProps {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}

const toneClass: Record<BadgeTone, string> = {
  ok: 'bg-ok/15 text-ok border-ok/30',
  warn: 'bg-warn/15 text-warn border-warn/30',
  danger: 'bg-danger/15 text-danger border-danger/30',
  info: 'bg-info/15 text-info border-info/30',
  muted: 'bg-raised text-ink-muted border-border',
};

/** 状态徽标。 */
export function Badge({ tone = 'muted', children, className }: BadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap',
        toneClass[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
