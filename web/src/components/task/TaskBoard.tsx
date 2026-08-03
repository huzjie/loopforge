import clsx from 'clsx';
import type { TaskRead } from '@/api/types';
import { TaskStatusBadge } from '@/components/run/RunStatusBadge';
import { formatRelativeTime } from '@/utils/format';
import { t } from '@/i18n';

export interface TaskCardProps {
  task: TaskRead;
  onClick?: (task: TaskRead) => void;
  active?: boolean;
}

/** 任务卡片（优先级/租约/Worker/重试计数）。 */
export function TaskCard({ task, onClick, active }: TaskCardProps) {
  return (
    <div
      onClick={onClick ? () => onClick(task) : undefined}
      className={clsx(
        'cursor-pointer rounded-md border bg-surface p-3 shadow-card transition-colors hover:border-brand/50',
        active ? 'border-brand' : 'border-border',
      )}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-ink-faint">{task.task_key}</span>
        <TaskStatusBadge state={task.state} />
      </div>
      <p className="mb-2 line-clamp-2 text-sm font-medium text-ink">{task.title}</p>
      <div className="flex items-center gap-3 text-xs text-ink-muted">
        <span>
          {t('task.priority')}: <span className="lf-num">{task.priority}</span>
        </span>
        <span>
          {t('task.attempts')}: <span className="lf-num">{task.attempt_count}/{task.max_attempts}</span>
        </span>
        {task.current_lease_id && <span className="truncate" title={task.current_lease_id}>🔒</span>}
        <span className="ml-auto text-ink-faint">{formatRelativeTime(task.updated_at)}</span>
      </div>
    </div>
  );
}

export interface TaskBoardProps {
  tasks: TaskRead[] | undefined;
  loading?: boolean;
  onTaskClick?: (task: TaskRead) => void;
  activeTaskId?: string | null;
}

const COLUMNS: { state: TaskRead['state']; title: string }[] = [
  { state: 'CREATED', title: '待创建' },
  { state: 'READY', title: '就绪' },
  { state: 'LEASED', title: '已租约' },
  { state: 'ACTIVE', title: '执行中' },
  { state: 'IN_REVIEW', title: '评审中' },
  { state: 'DONE', title: '完成' },
  { state: 'FAILED', title: '失败' },
  { state: 'BLOCKED', title: '阻塞' },
];

function groupByState(tasks: TaskRead[]): Record<string, TaskRead[]> {
  const groups: Record<string, TaskRead[]> = {};
  for (const task of tasks) {
    (groups[task.state] ??= []).push(task);
  }
  return groups;
}

/** Issue 看板（按状态分列）。拖拽能力在 W3 收口，本阶段提供点击选中。 */
export function TaskBoard({ tasks, loading, onTaskClick, activeTaskId }: TaskBoardProps) {
  if (loading && !tasks) {
    return <div className="grid grid-cols-2 gap-3 md:grid-cols-4">{Array.from({ length: 8 }).map((_, i) => (
      <div key={i} className="h-24 animate-pulse rounded-md bg-raised" />
    ))}</div>;
  }

  if (!tasks || tasks.length === 0) {
    return <div className="py-10 text-center text-sm text-ink-muted">{t('common.empty')}</div>;
  }

  const groups = groupByState(tasks);

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {COLUMNS.map((col) => {
        const items = groups[col.state] ?? [];
        return (
          <div key={col.state} className="rounded-md border border-border bg-raised/40 p-2">
            <div className="mb-2 flex items-center justify-between px-1">
              <span className="text-xs font-semibold text-ink-muted">{col.title}</span>
              <span className="lf-num text-xs text-ink-faint">{items.length}</span>
            </div>
            <div className="space-y-2">
              {items.map((task) => (
                <TaskCard
                  key={task.id}
                  task={task}
                  active={task.id === activeTaskId}
                  onClick={onTaskClick}
                />
              ))}
              {items.length === 0 && <div className="h-10 rounded-md border border-dashed border-border" />}
            </div>
          </div>
        );
      })}
    </div>
  );
}
