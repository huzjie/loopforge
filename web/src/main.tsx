import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from 'react-router-dom';
import { router } from './router';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import { loadDict } from '@/i18n';
import { zhCN } from '@/i18n/zh-CN';
import { enUS } from '@/i18n/en-US';
import { useI18nStore } from '@/i18n';
import './styles/index.css';

/**
 * React 入口：QueryClient、Router、i18n 字典装配、错误边界。
 * 主题初始化由 index.html 的防闪烁脚本完成（useTheme 负责切换与持久化）。
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
  },
});

// 初始化 i18n 字典（与 localStorage 中保存的语言一致）
const savedLocale = useI18nStore.getState().locale;
loadDict(savedLocale, savedLocale === 'zh-CN' ? zhCN : enUS);

const container = document.getElementById('root');
if (!container) {
  throw new Error('root 容器不存在');
}

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
