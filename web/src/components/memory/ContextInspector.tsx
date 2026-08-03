import type { ContextPack } from '@/api/types';
import { formatTokens } from '@/utils/format';

export interface ContextInspectorProps {
  data: ContextPack | undefined;
  loading?: boolean;
}

/** 上下文包检视器（分层 token 占比条）。 */
export function ContextInspector({ data, loading }: ContextInspectorProps) {
  if (loading && !data) {
    return <div className="h-32 animate-pulse rounded-md bg-raised" />;
  }

  if (!data || data.blocks.length === 0) {
    return <div className="py-6 text-center text-sm text-ink-muted">暂无上下文数据</div>;
  }

  const total = data.total_tokens || data.blocks.reduce((sum, b) => sum + b.tokens, 0);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-sm">
        <span className="text-ink">总 token</span>
        <span className="lf-num font-medium text-ink">{formatTokens(total)}</span>
      </div>
      {data.blocks.map((block) => {
        const ratio = total > 0 ? block.tokens / total : 0;
        return (
          <div key={block.layer}>
            <div className="mb-0.5 flex items-center justify-between text-xs">
              <span className="font-medium text-ink-muted">{block.layer}</span>
              <span className="lf-num text-ink-faint">
                {formatTokens(block.tokens)} · {block.items.length} 条
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-raised">
              <div className="h-full rounded-full bg-brand" style={{ width: `${ratio * 100}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
