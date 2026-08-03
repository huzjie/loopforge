import type { ReactNode } from 'react';
import clsx from 'clsx';
import { Button } from './Button';
import { EmptyState } from './EmptyState';
import { getLastTraceId } from '@/api/client';
import { t } from '@/i18n';

export interface ColumnDef<T> {
  key: string;
  header: string;
  /** 自定义单元格渲染；缺省取 row[key]。 */
  render?: (row: T) => ReactNode;
  /** 排序字段名；不设置则不可排序。 */
  sortKey?: string;
  className?: string;
  headerClassName?: string;
}

export interface DataTableProps<T> {
  columns: ColumnDef<T>[];
  rows: T[] | undefined;
  loading?: boolean;
  error?: unknown;
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  nextCursor?: string | null;
  hasMore?: boolean;
  total?: number;
  onLoadMore?: () => void;
  emptyTitle?: string;
  emptyDescription?: ReactNode;
  emptyAction?: ReactNode;
}

/** 通用表格：加载骨架 / 空态 / 错误态（带 trace_id）/ 游标分页。 */
export function DataTable<T>({
  columns,
  rows,
  loading = false,
  error,
  rowKey,
  onRowClick,
  nextCursor,
  hasMore = false,
  total,
  onLoadMore,
  emptyTitle,
  emptyDescription,
  emptyAction,
}: DataTableProps<T>) {
  if (error) {
    const message = error instanceof Error ? error.message : String(error);
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-center">
        <p className="text-sm text-danger">{message}</p>
        <p className="font-mono text-xs text-ink-faint">trace_id: {getLastTraceId() || '—'}</p>
      </div>
    );
  }

  if (!loading && (!rows || rows.length === 0)) {
    return <EmptyState title={emptyTitle ?? t('common.empty')} description={emptyDescription} action={emptyAction} />;
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-border bg-raised/50">
              {columns.map((col) => (
                <th key={col.key} className={clsx('lf-th', col.headerClassName)}>
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 5 }).map((_, i) => (
                  <tr key={`skeleton-${i}`} className="border-b border-border/60">
                    {columns.map((col) => (
                      <td key={col.key} className="lf-td">
                        <div className="h-3.5 w-full max-w-[120px] animate-pulse rounded bg-raised" />
                      </td>
                    ))}
                  </tr>
                ))
              : rows?.map((row) => (
                  <tr
                    key={rowKey(row)}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    className={clsx(
                      'border-b border-border/60 transition-colors hover:bg-raised/50',
                      onRowClick && 'cursor-pointer',
                    )}
                  >
                    {columns.map((col) => (
                      <td key={col.key} className={clsx('lf-td', col.className)}>
                        {col.render
                          ? col.render(row)
                          : ((row as Record<string, unknown>)[col.key] as ReactNode) ?? '—'}
                      </td>
                    ))}
                  </tr>
                ))}
          </tbody>
        </table>
      </div>

      {(hasMore || total !== undefined) && (
        <div className="flex items-center justify-between border-t border-border px-3 py-2">
          <span className="text-xs text-ink-faint">{total !== undefined ? `共 ${total} 条` : ''}</span>
          {hasMore && (
            <Button size="sm" variant="ghost" onClick={onLoadMore} disabled={loading}>
              {t('common.next')}
            </Button>
          )}
        </div>
      )}
      {nextCursor === null && hasMore === false && rows && rows.length > 0 && (
        <div className="border-t border-border px-3 py-2 text-center text-xs text-ink-faint">
          {t('common.noMore')}
        </div>
      )}
    </div>
  );
}
