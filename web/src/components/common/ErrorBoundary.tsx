import { Component, type ErrorInfo, type ReactNode } from 'react';
import { getLastTraceId } from '@/api/client';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
  traceId: string;
}

/**
 * 错误边界：捕获渲染期异常，展示 trace_id 便于排查。
 * trace_id 来自最近一次 API 响应（见 client.ts getLastTraceId）。
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null, traceId: '' };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error, traceId: getLastTraceId() };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 生产可上报到 /events/export 或日志通道
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  render(): ReactNode {
    const { error, traceId } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback;

    return (
      <div className="flex h-full min-h-[240px] flex-col items-center justify-center gap-3 p-6 text-center">
        <div className="text-lg font-semibold text-danger">出错了 / Something went wrong</div>
        <pre className="max-w-full overflow-auto rounded-md bg-raised p-3 text-xs text-ink-muted">
          {error.message}
        </pre>
        {traceId && (
          <div className="font-mono text-xs text-ink-faint">
            trace_id: <span className="text-ink-muted">{traceId}</span>
          </div>
        )}
        <button
          className="mt-2 rounded-md bg-brand px-4 py-1.5 text-sm text-white hover:opacity-90"
          onClick={() => this.setState({ error: null })}
        >
          重试 / Retry
        </button>
      </div>
    );
  }
}
