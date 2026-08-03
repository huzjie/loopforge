import { useNavigate } from 'react-router-dom';
import { DataTable, type ColumnDef } from '@/components/common/DataTable';
import { RunStatusBadge } from './RunStatusBadge';
import type { RunRead } from '@/api/types';
import { formatRelativeTime } from '@/utils/format';
import { useRuns } from '@/hooks/useRuns';
import { useAuthStore } from '@/stores/authStore';
import { t } from '@/i18n';

export interface RunTableProps {
  params?: Record<string, unknown>;
  onLoadMore?: () => void;
}

const ACTIVE_STATES = new Set(['PLANNING', 'RUNNING', 'PAUSING', 'PAUSED', 'RECONCILING', 'RECOVERING', 'AWAITING_APPROVAL']);

/** Run 列表表格（状态/进度/成本/耗时/操作）。 */
export function RunTable({ params, onLoadMore }: RunTableProps) {
  const navigate = useNavigate();
  const { data, isLoading, error, fetchNextPage, hasNextPage, isFetchingNextPage } = useRuns(params);
  const can = useAuthStore((state) => state.can);
  const rows = data?.pages.flatMap((page) => page.items);
  const total = data?.pages[0]?.total;

  const columns: ColumnDef<RunRead>[] = [
    {
      key: 'state',
      header: t('run.state'),
      render: (row) => <RunStatusBadge state={row.state} />,
    },
    {
      key: 'goal',
      header: t('run.goal'),
      className: 'max-w-[320px]',
      render: (row) => <span className="block truncate" title={row.goal}>{row.goal}</span>,
    },
    {
      key: 'loop_index',
      header: t('run.loops'),
      render: (row) => (
        <span className="lf-num">
          {row.loop_index}/{row.max_loops}
        </span>
      ),
    },
    {
      key: 'recovery_count',
      header: '恢复',
      render: (row) => (row.recovery_count > 0 ? <span className="text-warn">{row.recovery_count}</span> : '0'),
    },
    {
      key: 'created_at',
      header: t('common.createdAt'),
      render: (row) => <span className="text-ink-muted">{formatRelativeTime(row.created_at)}</span>,
    },
    ...(can('admin')
      ? ([
          {
            key: 'actions',
            header: t('common.actions'),
            render: (row: RunRead) => (
              <button
                className="text-xs text-brand hover:underline"
                onClick={(event) => {
                  event.stopPropagation();
                  void navigate(`/runs/${row.id}`);
                }}
              >
                {t('common.viewDetail')}
              </button>
            ),
          },
        ] as ColumnDef<RunRead>[])
      : []),
  ];

  return (
    <DataTable<RunRead>
      columns={columns}
      rows={rows}
      loading={isLoading || isFetchingNextPage}
      error={error}
      rowKey={(row) => row.id}
      onRowClick={(row) => void navigate(`/runs/${row.id}`)}
      hasMore={hasNextPage}
      onLoadMore={onLoadMore ?? (() => void fetchNextPage())}
      total={total}
    />
  );
}

export { ACTIVE_STATES };
