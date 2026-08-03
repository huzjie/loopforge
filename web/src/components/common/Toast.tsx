import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import clsx from 'clsx';

export type ToastTone = 'info' | 'success' | 'warning' | 'error';

interface ToastItem {
  id: number;
  tone: ToastTone;
  message: string;
}

interface ToastContextValue {
  push: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastContextValue>({ push: () => undefined });

const toneClass: Record<ToastTone, string> = {
  info: 'border-info/40 text-info',
  success: 'border-ok/40 text-ok',
  warning: 'border-warn/40 text-warn',
  error: 'border-danger/40 text-danger',
};

/** Toast 提供者：push(message, tone) 弹轻提示，3s 自动消失。 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const push = useCallback((message: string, tone: ToastTone = 'info') => {
    const id = Date.now() + Math.random();
    setItems((prev) => [...prev, { id, tone, message }]);
    setTimeout(() => {
      setItems((prev) => prev.filter((item) => item.id !== id));
    }, 3000);
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed top-4 right-4 z-[100] flex w-80 flex-col gap-2">
        {items.map((item) => (
          <div
            key={item.id}
            className={clsx(
              'pointer-events-auto rounded-md border bg-surface px-3 py-2 text-sm shadow-pop',
              toneClass[item.tone],
            )}
          >
            {item.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  return useContext(ToastContext);
}
