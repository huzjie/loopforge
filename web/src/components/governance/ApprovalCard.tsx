import { useState } from 'react';
import clsx from 'clsx';
import { Button } from '@/components/common/Button';
import { Badge, type BadgeTone } from '@/components/common/Badge';
import { JsonViewer } from '@/components/common/JsonViewer';
import { useApprovalDecision } from '@/hooks/useApprovals';
import { secondsUntil } from '@/utils/time';
import { formatRelativeTime } from '@/utils/format';
import { t } from '@/i18n';
import type { ApprovalDetail, ApprovalRead } from '@/api/types';

export interface ApprovalCardProps {
  approval: ApprovalRead;
}

const stateTone: Record<string, BadgeTone> = {
  PENDING: 'warn',
  APPROVED: 'ok',
  REJECTED: 'danger',
  EXPIRED: 'muted',
  TIMEOUT: 'muted',
};

/** 单条审批卡片（动作详情、风险说明、批准/拒绝）。 */
export function ApprovalCard({ approval }: ApprovalCardProps) {
  const decision = useApprovalDecision(approval.id);
  const [reason, setReason] = useState('');
  const [showDetail, setShowDetail] = useState(false);
  const pending = approval.state === 'PENDING';
  const remaining = secondsUntil(approval.expires_at);

  return (
    <div className="rounded-md border border-border bg-surface p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="mb-1 flex items-center gap-2">
            <Badge tone={stateTone[approval.state] ?? 'muted'}>{approval.state}</Badge>
            <span className="font-mono text-xs text-ink">{approval.action_kind}</span>
            {pending && approval.expires_at && (
              <span className={clsx('lf-num text-xs', remaining < 60 ? 'text-danger' : 'text-ink-faint')}>
                剩余 {remaining}s
              </span>
            )}
          </div>
          <p className="text-sm text-ink">{approval.risk_note || '（无风险说明）'}</p>
          {approval.rule_id && <p className="mt-0.5 font-mono text-xs text-ink-faint">rule: {approval.rule_id}</p>}
          <p className="mt-1 text-xs text-ink-faint">
            run {approval.run_id.slice(-8)} · {formatRelativeTime(approval.created_at)}
            {approval.decided_by ? ` · ${approval.decided_by}` : ''}
          </p>
        </div>
        <button
          className="shrink-0 text-xs text-ink-muted hover:text-ink"
          onClick={() => setShowDetail((prev) => !prev)}
        >
          {showDetail ? '收起' : '详情'}
        </button>
      </div>

      {showDetail && (approval as ApprovalDetail).action_payload && Object.keys((approval as ApprovalDetail).action_payload).length > 0 && (
        <div className="mt-2">
          <JsonViewer value={(approval as ApprovalDetail).action_payload} defaultDepth={2} maxHeight="160px" />
        </div>
      )}

      {pending && (
        <div className="mt-3 space-y-2">
          <input
            className="h-8 w-full rounded-md border border-border bg-surface px-2 text-sm text-ink focus:border-brand focus:outline-none"
            placeholder="决策理由（可选）"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="primary"
              loading={decision.isPending}
              onClick={() => decision.mutate({ approve: true, reason })}
            >
              {t('common.confirm')}
            </Button>
            <Button
              size="sm"
              variant="danger"
              loading={decision.isPending}
              onClick={() => decision.mutate({ approve: false, reason })}
            >
              拒绝
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
