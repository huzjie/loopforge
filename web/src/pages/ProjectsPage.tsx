import { useState } from 'react';
import { Card } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import { Modal } from '@/components/common/Modal';
import { DataTable, type ColumnDef } from '@/components/common/DataTable';
import { useProjects } from '@/hooks/useProjects';
import { useCreateProject } from '@/hooks/useProjects';
import { useToast } from '@/components/common/Toast';
import { formatRelativeTime } from '@/utils/format';
import { t } from '@/i18n';
import type { ProjectRead } from '@/api/types';

/** 项目与仓库管理。 */
export default function ProjectsPage() {
  const { data, isLoading, error } = useProjects();
  const createProject = useCreateProject();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [description, setDescription] = useState('');

  const columns: ColumnDef<ProjectRead>[] = [
    { key: 'slug', header: 'slug', render: (row) => <span className="font-mono text-ink">{row.slug}</span> },
    { key: 'name', header: '名称', render: (row) => <span className="font-medium text-ink">{row.name}</span> },
    { key: 'description', header: '描述', render: (row) => <span className="text-ink-muted">{row.description ?? '—'}</span> },
    { key: 'created_at', header: t('common.createdAt'), render: (row) => <span className="text-xs text-ink-muted">{formatRelativeTime(row.created_at)}</span> },
  ];

  const submit = () => {
    createProject.mutate(
      { name, slug, description: description || undefined },
      {
        onSuccess: () => {
          toast.push('项目已创建', 'success');
          setOpen(false);
          setName('');
          setSlug('');
          setDescription('');
        },
        onError: (err) => toast.push(err.message, 'error'),
      },
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-ink">{t('nav.projects')}</h1>
        <Button variant="primary" onClick={() => setOpen(true)}>
          + {t('common.create')}
        </Button>
      </div>

      <Card title="项目">
        <DataTable<ProjectRead>
          columns={columns}
          rows={data?.items}
          loading={isLoading}
          error={error}
          rowKey={(row) => row.id}
          total={data?.total}
        />
      </Card>

      <Modal
        open={open}
        title="新建项目"
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" loading={createProject.isPending} onClick={submit}>
              {t('common.confirm')}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <label className="block text-xs text-ink-muted">
            Slug *
            <input
              className="h-9 w-full rounded-md border border-border bg-surface px-2 text-sm text-ink focus:border-brand focus:outline-none"
              value={slug}
              onChange={(event) => setSlug(event.target.value)}
              placeholder="my-project"
            />
          </label>
          <label className="block text-xs text-ink-muted">
            名称 *
            <input
              className="h-9 w-full rounded-md border border-border bg-surface px-2 text-sm text-ink focus:border-brand focus:outline-none"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="block text-xs text-ink-muted">
            描述
            <textarea
              className="mt-1 h-20 w-full rounded-md border border-border bg-surface p-2 text-sm text-ink focus:border-brand focus:outline-none"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
        </div>
      </Modal>
    </div>
  );
}
