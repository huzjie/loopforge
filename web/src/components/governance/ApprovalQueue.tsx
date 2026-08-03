import { ApprovalCard } from './ApprovalCard';
import type { ApprovalRead } from '@/api/types';

export interface ApprovalQueueProps {
  approvals: ApprovalRead[] | undefined;
  loading?: boolean;
}

/** 审批队列（待办优先）。 */
export function ApprovalQueue({ approvals, loading }: ApprovalQueueProps) {
  if (loading && !approvals) {
    return <div className="space-y-3">{Array.from({ length: 3 }).map((_, i) => (
      <div key={i} className="h-20 animate-pulse rounded-md bg-raised" />
    ))}</div>;
  }

  if (!approvals || approvals.length === 0) {
    return <div className="py-10 text-center text-sm text-ink-muted">暂无待审批项</div>;
  }

  const sorted = [...approvals].sort((a, b) => {
    const rank = (state: string) => (state === 'PENDING' ? 0 : 1);
    return rank(a.state) - rank(b.state) || new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
  });

  return (
    <div className="space-y-3">
      {sorted.map((approval) => (
        <ApprovalCard key={approval.id} approval={approval} />
      ))}
    </div>
  );
}
