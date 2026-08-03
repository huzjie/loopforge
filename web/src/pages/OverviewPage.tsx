import { Link } from 'react-router-dom';
import { Card } from '@/components/common/Card';
import { RunStatusBadge } from '@/components/run/RunStatusBadge';
import { useRuns } from '@/hooks/useRuns';
import { useCostSummary } from '@/hooks/useCosts';
import { useApprovalSummary } from '@/hooks/useApprovals';
import { useHealthDetail } from '@/hooks/useHealth';
import { formatRelativeTime, formatUsd } from '@/utils/format';
import { t } from '@/i18n';

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card padding={false} className="p-4">
      <p className="text-xs text-ink-muted">{label}</p>
      <p className="lf-num mt-1 text-2xl font-semibold text-ink">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-ink-faint">{sub}</p>}
    </Card>
  );
}

/** 总览大盘：活跃 Run、今日成本、成功率、告警、队列深度。 */
export default function OverviewPage() {
  const { data: runs } = useRuns({ limit: 50 });

  const allRuns = runs?.pages.flatMap((page) => page.items) ?? [];
  const { data: cost } = useCostSummary();
  const { data: approvals } = useApprovalSummary();
  const { data: health } = useHealthDetail();

  const activeRuns = allRuns.filter((run) =>
    ['PLANNING', 'RUNNING', 'PAUSING', 'PAUSED', 'RECONCILING', 'RECOVERING', 'AWAITING_APPROVAL'].includes(run.state),
  ).length;
  const recent = allRuns.slice(0, 8);
  const failedCount = allRuns.filter((run) => run.state === 'FAILED').length;
  const successRate = runs?.pages[0]?.total ? ((runs.pages[0].total - failedCount) / runs.pages[0].total) * 100 : 0;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold text-ink">{t('overview.title')}</h1>
        <p className="text-xs text-ink-faint">{health ? `API v${health.version} · ${health.status}` : '…'}</p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label={t('overview.activeRuns')} value={String(activeRuns)} sub={t('overview.queueDepth')} />
        <StatCard label={t('overview.todayCost')} value={formatUsd(cost?.today_usd)} sub={t('cost.rate')} />
        <StatCard
          label={t('overview.successRate')}
          value={`${successRate.toFixed(1)}%`}
          sub={`${runs?.pages[0]?.total ?? 0} runs`}
        />
        <StatCard label={t('overview.pendingApprovals')} value={String(approvals?.pending ?? 0)} />
      </div>

      <Card title={t('overview.recentRuns')} actions={<Link to="/runs" className="text-xs text-brand hover:underline">{t('overview.viewAll')}</Link>}>
        {recent.length === 0 ? (
          <p className="py-8 text-center text-sm text-ink-muted">{t('common.empty')}</p>
        ) : (
          <div className="divide-y divide-border/60">
            {recent.map((run) => (
              <Link key={run.id} to={`/runs/${run.id}`} className="flex items-center gap-3 py-2 hover:bg-raised/40">
                <RunStatusBadge state={run.state} />
                <span className="min-w-0 flex-1 truncate text-sm text-ink" title={run.goal}>
                  {run.goal}
                </span>
                <span className="lf-num shrink-0 text-xs text-ink-muted">{run.loop_index}/{run.max_loops}</span>
                <span className="shrink-0 text-xs text-ink-faint">{formatRelativeTime(run.created_at)}</span>
              </Link>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
