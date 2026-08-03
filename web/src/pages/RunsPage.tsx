import { useState } from 'react';
import { Card } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import { Modal } from '@/components/common/Modal';
import { RunTable } from '@/components/run/RunTable';
import { useCreateRun } from '@/hooks/useRuns';
import { useToast } from '@/components/common/Toast';
import { t } from '@/i18n';

/** Run 列表与创建。 */
export default function RunsPage() {
  const [open, setOpen] = useState(false);
  const [projectId, setProjectId] = useState('');
  const [goal, setGoal] = useState('');
  const createRun = useCreateRun();
  const toast = useToast();

  const submit = () => {
    createRun.mutate(
      { project_id: projectId, goal, auto_start: true },
      {
        onSuccess: () => {
          toast.push('Run 已创建', 'success');
          setOpen(false);
          setGoal('');
        },
        onError: (error) => toast.push(error.message, 'error'),
      },
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-ink">{t('run.list')}</h1>
        <Button variant="primary" onClick={() => setOpen(true)}>
          + {t('run.create')}
        </Button>
      </div>

      <Card>
        <RunTable />
      </Card>

      <Modal
        open={open}
        title={t('run.create')}
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" loading={createRun.isPending} onClick={submit}>
              {t('common.confirm')}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <label className="block text-xs text-ink-muted">
            Project ID *
            <input
              className="h-9 w-full rounded-md border border-border bg-surface px-2 text-sm text-ink focus:border-brand focus:outline-none"
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
            />
          </label>
          <label className="block text-xs text-ink-muted">
            {t('run.goal')} *
            <textarea
              className="mt-1 h-24 w-full rounded-md border border-border bg-surface p-2 text-sm text-ink focus:border-brand focus:outline-none"
              placeholder={t('run.createGoal')}
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
            />
          </label>
        </div>
      </Modal>
    </div>
  );
}
