import { useParams, useNavigate } from 'react-router-dom';
import { Card } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { Tabs } from '@/components/common/Tabs';
import { RunStatusBadge } from '@/components/run/RunStatusBadge';
import { RunControls } from '@/components/run/RunControls';
import { RunTimeline } from '@/components/run/RunTimeline';
import { EventStreamPanel } from '@/components/event/EventStreamPanel';
import { TaskBoard } from '@/components/task/TaskBoard';
import { TaskDetailPanel } from '@/components/task/TaskDetailPanel';
import { ChainVerifyBadge } from '@/components/audit/ChainVerifyBadge';
import { useRunDetail, useRunTimeline } from '@/hooks/useRunDetail';
import { useTasks } from '@/hooks/useTasks';
import { useEventStream } from '@/hooks/useEventStream';
import { useEvents } from '@/hooks/useEvents';
import { useState } from 'react';
import { formatRelativeTime, formatTokens, formatUsd } from '@/utils/format';
import { t } from '@/i18n';

type TabKey = 'overview' | 'tasks' | 'events' | 'timeline' | 'audit';

/** Run 详情：概览 + 任务 + 事件流 + 时间轴 + 审计。 */
export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [tab, setTab] = useState<TabKey>('overview');
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);

  const { data: run, isLoading } = useRunDetail(id);
  const { data: timeline } = useRunTimeline(id);
  const { data: tasks } = useTasks({ run_id: id, limit: 200 });
  const { data: eventPage } = useEvents({ run_id: id, limit: 200 });

  // 实时订阅
  useEventStream({ runId: id });

  if (isLoading && !run) {
    return <div className="h-64 animate-pulse rounded-md bg-raised" />;
  }

  if (!run) {
    return (
      <div className="py-16 text-center">
        <p className="text-danger">Run 不存在</p>
        <button className="mt-2 text-sm text-brand hover:underline" onClick={() => navigate('/runs')}>
          返回列表
        </button>
      </div>
    );
  }

  const stats = run.stats;

  const tabs = [
    { key: 'overview' as const, label: '概览' },
    { key: 'tasks' as const, label: '任务', badge: stats?.task_total ?? 0 },
    { key: 'events' as const, label: '事件流', badge: stats?.event_count ?? 0 },
    { key: 'timeline' as const, label: '时间轴' },
    { key: 'audit' as const, label: '审计' },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <button className="text-xs text-ink-muted hover:text-ink" onClick={() => navigate('/runs')}>
          ← {t('run.list')}
        </button>
        <h1 className="text-lg font-semibold text-ink">Run · {run.id.slice(-12)}</h1>
        <RunStatusBadge state={run.state} />
        {run.recovery_count > 0 && <Badge tone="warn">恢复 ×{run.recovery_count}</Badge>}
        <div className="ml-auto">
          <RunControls runId={run.id} state={run.state} />
        </div>
      </div>

      <Card padding={false} className="p-4">
        <p className="text-sm text-ink">{run.goal}</p>
        <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
          <div>
            <p className="text-xs text-ink-faint">{t('run.loops')}</p>
            <p className="lf-num text-sm text-ink">{run.loop_index}/{run.max_loops}</p>
          </div>
          <div>
            <p className="text-xs text-ink-faint">{t('run.cost')}</p>
            <p className="lf-num text-sm text-ink">{formatUsd(stats?.cost_usd)}</p>
          </div>
          <div>
            <p className="text-xs text-ink-faint">tokens</p>
            <p className="lf-num text-sm text-ink">
              {formatTokens(stats?.prompt_tokens)}/{formatTokens(stats?.completion_tokens)}
            </p>
          </div>
          <div>
            <p className="text-xs text-ink-faint">{t('common.updatedAt')}</p>
            <p className="text-sm text-ink">{formatRelativeTime(run.updated_at)}</p>
          </div>
        </div>
      </Card>

      <Tabs items={tabs} active={tab} onChange={(key) => setTab(key as TabKey)} />

      {tab === 'overview' && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="任务统计">
            <div className="grid grid-cols-2 gap-2 text-sm">
              <span className="text-ink-muted">task_total</span><span className="lf-num text-ink">{stats?.task_total ?? 0}</span>
              <span className="text-ink-muted">task_done</span><span className="lf-num text-ok">{stats?.task_done ?? 0}</span>
              <span className="text-ink-muted">task_failed</span><span className="lf-num text-danger">{stats?.task_failed ?? 0}</span>
              <span className="text-ink-muted">task_active</span><span className="lf-num text-info">{stats?.task_active ?? 0}</span>
              <span className="text-ink-muted">step_unconfirmed</span><span className="lf-num text-warn">{stats?.step_unconfirmed ?? 0}</span>
              <span className="text-ink-muted">pending_approvals</span><span className="lf-num text-warn">{stats?.pending_approvals ?? 0}</span>
            </div>
          </Card>
          <Card title={t('run.timeline')}>
            <RunTimeline data={timeline} />
          </Card>
        </div>
      )}

      {tab === 'tasks' && (
        <Card>
          <TaskBoard tasks={tasks?.items} onTaskClick={(task) => setActiveTaskId(task.id)} activeTaskId={activeTaskId} />
        </Card>
      )}

      {tab === 'events' && (
        <Card padding={false}>
          <EventStreamPanel runId={id} initialEvents={eventPage?.items ?? []} height="520px" />
        </Card>
      )}

      {tab === 'timeline' && (
        <Card>
          <RunTimeline data={timeline} />
        </Card>
      )}

      {tab === 'audit' && (
        <Card title="审计" actions={<ChainVerifyBadge runId={id} />}>
          <p className="text-sm text-ink-muted">
            事件表以 seq 单调递增并链接哈希，可校验完整性。审计详情页在 W3 收口。
          </p>
        </Card>
      )}

      <TaskDetailPanel taskId={activeTaskId} onClose={() => setActiveTaskId(null)} />
    </div>
  );
}
