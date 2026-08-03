import { Suspense } from 'react';
import { Outlet } from 'react-router-dom';
import { AppShell, PageLoader } from '@/components/layout/AppShell';

/** 应用外壳：AppShell 包裹路由出口，懒加载页面统一走 PageLoader。 */
export default function App() {
  return (
    <AppShell>
      <Suspense fallback={<PageLoader />}>
        <Outlet />
      </Suspense>
    </AppShell>
  );
}
