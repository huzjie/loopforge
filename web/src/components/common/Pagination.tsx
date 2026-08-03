import { Button } from './Button';
import { t } from '@/i18n';

export interface PaginationProps {
  /** 游标分页：下一页游标；null 表示到底。 */
  nextCursor: string | null;
  hasMore: boolean;
  onLoadMore: () => void;
  loading?: boolean;
  total?: number;
}

/** 游标分页控件。 */
export function Pagination({ nextCursor, hasMore, onLoadMore, loading = false, total }: PaginationProps) {
  if (!hasMore && total === undefined) return null;
  return (
    <div className="flex items-center justify-between border-t border-border px-3 py-2">
      <span className="text-xs text-ink-faint">{total !== undefined ? `共 ${total} 条` : ''}</span>
      {hasMore && (
        <Button size="sm" variant="ghost" onClick={onLoadMore} disabled={loading || !nextCursor}>
          {t('common.next')}
        </Button>
      )}
      {!hasMore && total !== undefined && <span className="text-xs text-ink-faint">{t('common.noMore')}</span>}
    </div>
  );
}
