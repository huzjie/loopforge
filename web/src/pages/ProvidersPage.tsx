import { useState } from 'react';
import { Card } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import { Modal } from '@/components/common/Modal';
import { ProviderList } from '@/components/provider/ProviderList';
import { ProviderForm } from '@/components/provider/ProviderForm';
import { t } from '@/i18n';
import type { ProviderConfigRead } from '@/api/types';

/** Provider 与角色模型绑定配置。 */
export default function ProvidersPage() {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ProviderConfigRead | null>(null);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-ink">{t('nav.providers')}</h1>
        <Button
          variant="primary"
          onClick={() => {
            setEditing(null);
            setOpen(true);
          }}
        >
          + 新增
        </Button>
      </div>

      <Card title="Provider 配置">
        <ProviderList
          onEdit={(provider) => {
            setEditing(provider);
            setOpen(true);
          }}
        />
      </Card>

      <Modal
        open={open}
        title={editing ? '编辑 Provider' : '新增 Provider'}
        onClose={() => setOpen(false)}
      >
        <ProviderForm
          provider={editing}
          onDone={() => setOpen(false)}
        />
      </Modal>
    </div>
  );
}
