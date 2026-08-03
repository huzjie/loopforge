import { useState } from 'react';
import { Badge, type BadgeTone } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { useAuditChain } from '@/hooks/useAudit';
import type { ChainVerifyResult } from '@/api/types';

/** 哈希链完整性校验结果徽标（调用 /audit/verify-chain）。 */
export function ChainVerifyBadge({ runId }: { runId?: string }) {
  const [result, setResult] = useState<ChainVerifyResult | null>(null);
  const { mutate, isPending } = useAuditChain();

  const verify = () => {
    mutate(
      runId ? { run_id: runId } : undefined,
      {
        onSuccess: (data) => setResult(data),
      },
    );
  };

  const tone: BadgeTone = result === null ? 'muted' : result.valid ? 'ok' : 'danger';

  return (
    <div className="flex items-center gap-2">
      <Button size="sm" variant="ghost" loading={isPending} onClick={verify}>
        校验哈希链
      </Button>
      {result && (
        <Badge tone={tone}>
          {result.valid ? `完整（${result.total} 条）` : `断链 @seq ${result.broken_at_seq}`}
        </Badge>
      )}
    </div>
  );
}
