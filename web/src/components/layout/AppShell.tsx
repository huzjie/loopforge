import type { ReactNode } from 'react';
import clsx from 'clsx';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { ToastProvider } from '@/components/common/Toast';
import { useUiStore } from '@/stores/uiStore';

/** 全局加载态（路由懒加载 fallback）。 */
export function PageLoader() {
  return (
    <div className="flex h-full min-h-[240px] items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand border-t-transparent" />
    </div>
  );
}

/** 应用外壳：侧栏 + 顶栏 + 内容区 + 全局 Toast 容器。 */
export function AppShell({ children }: { children: ReactNode }) {
  const sidebarCollapsed = useUiStore((state) => state.sidebarCollapsed);

  return (
    <ToastProvider>
      <div className="flex h-full min-h-screen">
        <Sidebar collapsed={sidebarCollapsed} />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar />
          <main
            className={clsx(
              'flex-1 overflow-y-auto p-4 md:p-6',
              sidebarCollapsed ? 'lg:pl-16' : 'lg:pl-64',
            )}
          >
            <div className="mx-auto w-full max-w-7xl">{children}</div>
          </main>
        </div>
      </div>
    </ToastProvider>
  );
}
