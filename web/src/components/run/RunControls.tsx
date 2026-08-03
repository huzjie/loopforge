import { useState } from 'react';
import { Button } from '@/components/common/Button';
import { Modal } from '@/components/common/Modal';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { runApi } from '@/api/endpoints';
import { queryKeys } from '@/api/queryKeys';
import { useRunControl } from '@/hooks/useRuns';
import { useAuthStore } from '@/stores/authStore';
import { t } from '@/i18n';
import type { RunControlAction } from '@/api/types';

export interface RunControlsProps {
  runId: string;
  state: string;
  onMutated?: () => void;
}

const ACTION_LABEL: Record<string, string> = {
  start: 'run.controls.start',
  pause: 'run.controls.pause',
  resume: 'run.controls.resume',
  cancel: 'run.controls.cancel',
  retry: 'run.controls.retry',
};

type ControlKind = 'start' | 'pause' | 'resume' | 'cancel' | 'retry';

/** start/pause/resume/cancel/retry 操作组（cancel 带二次确认）。 */
export function RunControls({ runId, state, onMutated }: RunControlsProps) {
  const control = useRunControl(runId);
  const can = useAuthStore((state) => state.can);
  const [confirmKind, setConfirmKind] = useState<ControlKind | null>(null);
  const queryClient = useQueryClient();

  // start / retry 走独立端点（不在 RunControlAction 枚举内）
  const special = useMutation({
    mutationFn: (kind: 'start' | 'retry') => (kind === 'start' ? runApi.start(runId) : runApi.retry(runId)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.detail(runId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.all });
      onMutated?.();
    },
  });

  if (!can('operator')) return null;

  const runAction = (kind: ControlKind) => {
    if (kind === 'start' || kind === 'retry') {
      special.mutate(kind);
      return;
    }
    const action: RunControlAction = { action: kind, reason: '' };
    control.mutate(action, {
      onSuccess: () => onMutated?.(),
    });
  };

  const available: ControlKind[] = [];
  if (state === 'CREATED' || state === 'READY') available.push('start');
  if (state === 'RUNNING' || state === 'PLANNING') available.push('pause');
  if (state === 'PAUSED' || state === 'HALTED') available.push('resume');
  if (state === 'FAILED') available.push('retry');
  if (!['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(state)) available.push('cancel');

  if (available.length === 0) return null;

  const pending = control.isPending || special.isPending;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {available.map((kind) => (
        <Button
          key={kind}
          size="sm"
          variant={kind === 'cancel' ? 'danger' : 'secondary'}
          loading={pending}
          onClick={() => {
            if (kind === 'cancel') setConfirmKind(kind);
            else runAction(kind);
          }}
        >
          {t(ACTION_LABEL[kind])}
        </Button>
      ))}

      <Modal
        open={confirmKind !== null}
        title={t('common.confirm')}
        onClose={() => setConfirmKind(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmKind(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              loading={pending}
              onClick={() => {
                if (confirmKind) runAction(confirmKind);
                setConfirmKind(null);
              }}
            >
              {t('common.confirm')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink">
          确认要执行「{t(ACTION_LABEL[confirmKind ?? 'cancel'])}」操作吗？
        </p>
      </Modal>
    </div>
  );
}
