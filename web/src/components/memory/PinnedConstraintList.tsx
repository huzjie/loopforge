import { Badge } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import type { PinnedConstraintRead } from '@/api/types';

export interface PinnedConstraintListProps {
  items: PinnedConstraintRead[] | undefined;
  loading?: boolean;
  onDelete?: (id: string) => void;
}

/** Pinned Constraints 管理（置顶/删除/来源溯源）。 */
export function PinnedConstraintList({ items, loading, onDelete }: PinnedConstraintListProps) {
  if (loading && !items) {
    return <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => (
      <div key={i} className="h-14 animate-pulse rounded-md bg-raised" />
    ))}</div>;
  }

  if (!items || items.length === 0) {
    return <div className="py-6 text-center text-sm text-ink-muted">暂无 Pinned 约束</div>;
  }

  const sorted = [...items].sort((a, b) => a.priority - b.priority);

  return (
    <div className="space-y-2">
      {sorted.map((item) => (
        <div key={item.id} className="flex items-start gap-2 rounded-md border border-border p-2">
          <Badge tone={item.active ? 'info' : 'muted'}>{item.priority}</Badge>
          <div className="min-w-0 flex-1">
            <p className="text-sm text-ink">{item.text_content}</p>
            <p className="mt-0.5 text-xs text-ink-faint">
              origin: {item.origin} · hit ×{item.hit_count}
            </p>
          </div>
          {onDelete && item.active && (
            <Button size="sm" variant="ghost" onClick={() => onDelete(item.id)}>
              停用
            </Button>
          )}
        </div>
      ))}
    </div>
  );
}
