import { Card } from '@/components/common/Card';
import { ApprovalQueue } from '@/components/governance/ApprovalQueue';
import { useApprovals } from '@/hooks/useApprovals';
import { t } from '@/i18n';

/** 审批中心。 */
export default function ApprovalsPage() {
  const { data, isLoading, error } = useApprovals({ limit: 100 });

  if (error) {
    return (
      <Card>
        <p className="text-sm text-danger">{error instanceof Error ? error.message : String(error)}</p>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-ink">{t('nav.approvals')}</h1>
      <Card title="审批队列">
        <ApprovalQueue approvals={data?.items} loading={isLoading} />
      </Card>
    </div>
  );
}
