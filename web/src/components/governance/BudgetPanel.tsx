import type { BudgetRead } from '@/api/types';
import { formatPercent, formatUsd } from '@/utils/format';

export interface BudgetPanelProps {
  budgets: BudgetRead[] | undefined;
  loading?: boolean;
}

/** 预算面板（额度/已用/速率/预计耗尽时间）。 */
export function BudgetPanel({ budgets, loading }: BudgetPanelProps) {
  if (loading && !budgets) {
    return <div className="space-y-3">{Array.from({ length: 3 }).map((_, i) => (
      <div key={i} className="h-16 animate-pulse rounded-md bg-raised" />
    ))}</div>;
  }

  if (!budgets || budgets.length === 0) {
    return <div className="py-8 text-center text-sm text-ink-muted">暂无预算</div>;
  }

  return (
    <div className="space-y-3">
      {budgets.map((budget) => {
        const limit = Number(budget.limit_usd);
        const consumed = Number(budget.consumed_usd);
        const ratio = limit > 0 ? consumed / limit : 0;
        const tone = ratio >= budget.halt_at ? 'bg-danger' : ratio >= budget.warn_at ? 'bg-warn' : 'bg-brand';

        return (
          <div key={budget.id} className="rounded-md border border-border p-3">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-sm font-medium text-ink">
                {budget.scope} · {budget.scope_ref}
              </span>
              <span className="lf-num text-xs text-ink-muted">
                {formatUsd(consumed)} / {formatUsd(limit)}
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-raised">
              <div
                className={`h-full rounded-full ${tone} transition-all`}
                style={{ width: `${Math.min(100, ratio * 100)}%` }}
              />
            </div>
            <div className="mt-1 flex justify-between text-xs text-ink-faint">
              <span>{formatPercent(ratio)}</span>
              <span>
                warn {formatPercent(budget.warn_at)} · halt {formatPercent(budget.halt_at)}
                {!budget.active && ' · 停用'}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
