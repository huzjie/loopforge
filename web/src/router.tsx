import { lazy } from 'react';
import { createBrowserRouter } from 'react-router-dom';
import App from './App';

/**
 * 路由表（13 页 + 懒加载 + 404）。
 * 页面组件用 default export，便于 React.lazy 加载。
 */
const OverviewPage = lazy(() => import('./pages/OverviewPage'));
const RunsPage = lazy(() => import('./pages/RunsPage'));
const RunDetailPage = lazy(() => import('./pages/RunDetailPage'));
const TaskBoardPage = lazy(() => import('./pages/TaskBoardPage'));
const ReplayPage = lazy(() => import('./pages/ReplayPage'));
const CostPage = lazy(() => import('./pages/CostPage'));
const PoliciesPage = lazy(() => import('./pages/PoliciesPage'));
const ApprovalsPage = lazy(() => import('./pages/ApprovalsPage'));
const ProvidersPage = lazy(() => import('./pages/ProvidersPage'));
const ProjectsPage = lazy(() => import('./pages/ProjectsPage'));
const HealthPage = lazy(() => import('./pages/HealthPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'));

export const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: 'runs', element: <RunsPage /> },
      { path: 'runs/:id', element: <RunDetailPage /> },
      { path: 'tasks', element: <TaskBoardPage /> },
      { path: 'tasks/run/:runId', element: <TaskBoardPage /> },
      { path: 'replay', element: <ReplayPage /> },
      { path: 'replay/:runId', element: <ReplayPage /> },
      { path: 'costs', element: <CostPage /> },
      { path: 'policies', element: <PoliciesPage /> },
      { path: 'approvals', element: <ApprovalsPage /> },
      { path: 'providers', element: <ProvidersPage /> },
      { path: 'projects', element: <ProjectsPage /> },
      { path: 'health', element: <HealthPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);
