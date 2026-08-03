import { useEffect, type ReactNode } from 'react';
import clsx from 'clsx';

export interface DrawerProps {
  open: boolean;
  title?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  width?: string;
}

/** 右侧抽屉（详情面板用）。ESC 与遮罩均可关闭。 */
export function Drawer({ open, title, onClose, children, width = 'w-[480px]' }: DrawerProps) {
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/30" onClick={onClose}>
      <aside
        className={clsx('absolute inset-y-0 right-0 flex flex-col bg-surface shadow-pop', width)}
        onClick={(event) => event.stopPropagation()}
        aria-label="drawer"
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h3 className="truncate text-sm font-semibold text-ink">{title}</h3>
          <button
            className="rounded p-1 text-ink-muted hover:bg-raised hover:text-ink"
            onClick={onClose}
            aria-label="close"
          >
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
      </aside>
    </div>
  );
}
