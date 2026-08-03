import { useEffect, type ReactNode } from 'react';
import clsx from 'clsx';

export interface ModalProps {
  open: boolean;
  title?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
}

/** 模态框：ESC 关闭、点击遮罩关闭、焦点陷阱。 */
export function Modal({ open, title, onClose, children, footer, width = 'max-w-lg' }: ModalProps) {
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
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className={clsx('w-full rounded-card border border-border bg-surface shadow-pop', width)}
        onClick={(event) => event.stopPropagation()}
      >
        {title !== undefined && (
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h3 className="text-sm font-semibold text-ink">{title}</h3>
            <button
              className="rounded p-1 text-ink-muted hover:bg-raised hover:text-ink"
              onClick={onClose}
              aria-label="close"
            >
              ✕
            </button>
          </div>
        )}
        <div className="max-h-[70vh] overflow-y-auto p-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-border px-4 py-3">{footer}</div>}
      </div>
    </div>
  );
}
