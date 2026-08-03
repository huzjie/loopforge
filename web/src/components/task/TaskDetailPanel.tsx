import { Drawer } from '@/components/common/Drawer';
import { JsonViewer } from '@/components/common/JsonViewer';
import { TaskStatusBadge } from '@/components/run/RunStatusBadge';
import { useTaskDetail, useTaskSteps } from '@/hooks/useTasks';
import { formatRelativeTime } from '@/utils/format';
import { t } from '@/i18n';
import type { StepRead } from '@/api/types';

export interface TaskDetailPanelProps {
  taskId: string | null;
  onClose: () => void;
}

/** 任务详情抽屉（步骤、事件、diff、PR）。 */
export function TaskDetailPanel({ taskId, onClose }: TaskDetailPanelProps) {
  const { data: task, isLoading } = useTaskDetail(taskId ?? undefined);
  const { data: steps } = useTaskSteps(taskId ?? undefined, { limit: 50 });

  return (
    <Drawer open={taskId !== null} title={task ? `${task.task_key} · ${task.title}` : t('common.loading')} onClose={onClose}>
      {isLoading && !task ? (
        <div className="space-y-3">
          <div className="h-24 animate-pulse rounded-md bg-raised" />
          <div className="h-40 animate-pulse rounded-md bg-raised" />
        </div>
      ) : task ? (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <TaskStatusBadge state={task.state} />
            <span className="text-xs text-ink-faint">v{task.version}</span>
          </div>

          <dl className="grid grid-cols-2 gap-2 text-sm">
            <div>
              <dt className="text-xs text-ink-faint">{t('task.priority')}</dt>
              <dd className="lf-num">{task.priority}</dd>
            </div>
            <div>
              <dt>{t('task.attempts')}</dt>
              <dd className="lf-num">{task.attempt_count}/{task.max_attempts}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-faint">reclaims</dt>
              <dd className="lf-num">{task.reclaim_count}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-faint">{t('common.updatedAt')}</dt>
              <dd className="text-xs">{formatRelativeTime(task.updated_at)}</dd>
            </div>
          </dl>

          {task.pr_url && (
            <a
              href={task.pr_url}
              target="_blank"
              rel="noreferrer"
              className="inline-block rounded-md bg-brand-soft px-2 py-1 text-xs text-brand hover:underline"
            >
              PR ↗
            </a>
          )}
          {task.branch_name && (
            <p className="font-mono text-xs text-ink-muted">branch: {task.branch_name}</p>
          )}

          <div>
            <h4 className="mb-1 text-xs font-semibold text-ink-muted">body</h4>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-raised p-2 text-xs text-ink">
              {task.body || '—'}
            </pre>
          </div>

          <div>
            <h4 className="mb-1 text-xs font-semibold text-ink-muted">Steps（{steps?.items.length ?? 0}）</h4>
            <div className="space-y-1.5">
              {steps?.items.map((step: StepRead) => (
                <div key={step.id} className="rounded-md border border-border p-2 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-ink">{step.phase} · L{step.loop_index}</span>
                    <span className="text-ink-muted">{step.state}</span>
                  </div>
                  {Object.keys(step.intent_payload).length > 0 && (
                    <div className="mt-1">
                      <JsonViewer value={step.intent_payload} defaultDepth={0} maxHeight="120px" />
                    </div>
                  )}
                </div>
              ))}
              {(!steps || steps.items.length === 0) && (
                <p className="text-xs text-ink-faint">{t('common.empty')}</p>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </Drawer>
  );
}
