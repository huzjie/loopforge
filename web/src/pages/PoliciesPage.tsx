import { Card } from '@/components/common/Card';
import { Tabs } from '@/components/common/Tabs';
import { PolicyEditor } from '@/components/governance/PolicyEditor';
import { DataTable, type ColumnDef } from '@/components/common/DataTable';
import { usePolicies } from '@/hooks/usePolicies';
import { Badge } from '@/components/common/Badge';
import { useState } from 'react';
import { formatRelativeTime } from '@/utils/format';
import { t } from '@/i18n';
import type { PolicyRead } from '@/api/types';

/** 策略管理与红线用例集。 */
export default function PoliciesPage() {
  const [tab, setTab] = useState<'list' | 'editor'>('list');
  const { data, isLoading, error } = usePolicies();

  const columns: ColumnDef<PolicyRead>[] = [
    { key: 'name', header: '策略名', render: (row) => <span className="font-medium text-ink">{row.name}</span> },
    { key: 'version', header: '版本', render: (row) => <span className="lf-num">v{row.version}</span> },
    {
      key: 'active',
      header: '状态',
      render: (row) => <Badge tone={row.active ? 'ok' : 'muted'}>{row.active ? '生效中' : '未激活'}</Badge>,
    },
    { key: 'checksum', header: 'checksum', render: (row) => <span className="font-mono text-xs text-ink-faint">{row.checksum.slice(0, 12)}</span> },
    { key: 'updated', header: '更新', render: (row) => <span className="text-xs text-ink-muted">{formatRelativeTime(row.created_at)}</span> },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-ink">{t('nav.policies')}</h1>
        <Tabs
          items={[
            { key: 'list', label: '策略列表' },
            { key: 'editor', label: '编辑器' },
          ]}
          active={tab}
          onChange={(key) => setTab(key as 'list' | 'editor')}
        />
      </div>

      {tab === 'list' ? (
        <Card>
          <DataTable<PolicyRead>
            columns={columns}
            rows={data?.items}
            loading={isLoading}
            error={error}
            rowKey={(row) => row.id}
            total={data?.meta.total}
          />
        </Card>
      ) : (
        <Card title="策略编辑器">
          <PolicyEditor />
        </Card>
      )}
    </div>
  );
}
