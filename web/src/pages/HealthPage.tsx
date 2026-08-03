import { Card } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { DataTable, type ColumnDef } from '@/components/common/DataTable';
import { useHealthDetail, useSystemInfo } from '@/hooks/useHealth';
import { useWorkers } from '@/hooks/useWorkers';
import { formatRelativeTime } from '@/utils/format';
import { t } from '@/i18n';
import type { WorkerRead } from '@/api/types';

/** 系统健康与 Worker 状态。 */
export default function HealthPage() {
  const { data: health } = useHealthDetail();
  const { data: info } = useSystemInfo();
  const { data: workers, isLoading, error } = useWorkers();

  const columns: ColumnDef<WorkerRead>[] = [
    { key: 'id', header: 'Worker', render: (row) => <span className="font-mono text-xs text-ink">{row.id.slice(-12)}</span> },
    { key: 'hostname', header: '主机', render: (row) => <span className="text-ink">{row.hostname || '—'}</span> },
    { key: 'state', header: '状态', render: (row) => <Badge tone={row.drain ? 'warn' : 'ok'}>{row.drain ? '排空' : row.state}</Badge> },
    { key: 'current_task_id', header: '当前任务', render: (row) => <span className="font-mono text-xs text-ink-faint">{row.current_task_id?.slice(-12) ?? '—'}</span> },
    { key: 'last_heartbeat_at', header: '心跳', render: (row) => <span className="text-xs text-ink-muted">{formatRelativeTime(row.last_heartbeat_at)}</span> },
  ];

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">{t('nav.health')}</h1>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="依赖健康">
          <div className="mb-3 flex items-center gap-2">
            <Badge tone={health?.status === 'healthy' ? 'ok' : health?.status === 'degraded' ? 'warn' : 'danger'}>
              {health?.status ?? 'unknown'}
            </Badge>
            {health && <span className="text-xs text-ink-faint">v{health.version} · {health.checked_at ? formatRelativeTime(health.checked_at) : ''}</span>}
          </div>
          <div className="space-y-1">
            {health ? (
              Object.entries(health.checks ?? {}).map(([name, ok]) => (
                <div key={name} className="flex items-center justify-between text-sm">
                  <span className="text-ink-muted">{name}</span>
                  <Badge tone={ok ? 'ok' : 'danger'}>{ok ? '✓' : '✗'}</Badge>
                </div>
              ))
            ) : (
              <p className="text-sm text-ink-muted">加载中…</p>
            )}
          </div>
        </Card>

        <Card title="系统信息">
          {info ? (
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between"><dt className="text-ink-muted">version</dt><dd className="font-mono text-ink">{info.version}</dd></div>
              <div className="flex justify-between"><dt className="text-ink-muted">build</dt><dd className="font-mono text-ink">{info.build}</dd></div>
              <div className="flex justify-between"><dt className="text-ink-muted">providers</dt><dd className="font-mono text-ink">{info.providers.join(', ') || '—'}</dd></div>
            </dl>
          ) : (
            <p className="text-sm text-ink-muted">加载中…</p>
          )}
        </Card>
      </div>

      <Card title="Worker 列表">
        <DataTable<WorkerRead>
          columns={columns}
          rows={workers?.items}
          loading={isLoading}
          error={error}
          rowKey={(row) => row.id}
          total={workers?.meta.total}
        />
      </Card>
    </div>
  );
}
