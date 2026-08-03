import { Card } from '@/components/common/Card';
import { CostTrendChart } from '@/components/charts/CostTrendChart';
import { TokenUsageChart } from '@/components/charts/TokenUsageChart';
import { useCostSummary, useCostTimeseries, useCostBreakdown } from '@/hooks/useCosts';
import { useBudgetList } from '@/hooks/useBudgets';
import { BudgetPanel } from '@/components/governance/BudgetPanel';
import { formatUsd } from '@/utils/format';
import { t } from '@/i18n';

/** 成本分析（五维下钻 + 时序 + 预算）。 */
export default function CostPage() {
  const { data: summary } = useCostSummary();
  const { data: timeseries } = useCostTimeseries({ days: 7 });
  const { data: breakdown } = useCostBreakdown({ dimension: 'provider' });
  const { data: budgets, isLoading: budgetsLoading } = useBudgetList();

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">{t('cost.title')}</h1>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Card padding={false} className="p-4">
          <p className="text-xs text-ink-muted">{t('cost.today')}</p>
          <p className="lf-num mt-1 text-2xl font-semibold text-ink">{formatUsd(summary?.today_usd)}</p>
        </Card>
        <Card padding={false} className="p-4">
          <p className="text-xs text-ink-muted">{t('cost.week')}</p>
          <p className="lf-num mt-1 text-2xl font-semibold text-ink">{formatUsd(summary?.week_usd)}</p>
        </Card>
        <Card padding={false} className="p-4">
          <p className="text-xs text-ink-muted">{t('cost.total')}</p>
          <p className="lf-num mt-1 text-2xl font-semibold text-ink">{formatUsd(summary?.total_usd)}</p>
        </Card>
        <Card padding={false} className="p-4">
          <p className="text-xs text-ink-muted">{t('cost.rate')}</p>
          <p className="lf-num mt-1 text-2xl font-semibold text-ink">{formatUsd(summary?.rate_usd_per_hour)}</p>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="成本时序（近 7 天）">
          <CostTrendChart data={timeseries?.items} height={260} />
        </Card>
        <Card title="Token 用量">
          <TokenUsageChart data={timeseries?.items ?? []} height={260} />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Provider 成本占比">
          <div className="space-y-2">
            {(breakdown?.aggregates ?? []).map((agg) => (
              <div key={agg.key} className="flex items-center justify-between text-sm">
                <span className="text-ink-muted">{agg.key}</span>
                <span className="lf-num text-ink">{formatUsd(agg.cost_usd)}</span>
              </div>
            ))}
            {(!breakdown || breakdown.aggregates.length === 0) && (
              <p className="py-6 text-center text-sm text-ink-muted">{t('common.empty')}</p>
            )}
          </div>
        </Card>
        <Card title="预算">
          <BudgetPanel budgets={budgets?.items} loading={budgetsLoading} />
        </Card>
      </div>
    </div>
  );
}
