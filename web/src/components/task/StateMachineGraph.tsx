import clsx from 'clsx';
import type { TaskState } from '@/api/types';
import { taskStateMeta } from '@/utils/format';

export interface StateMachineGraphProps {
  state: TaskState | string;
  /** 合法转移目标（可空，缺省显示全部）。 */
  allowed?: string[];
  onTransition?: (to: string) => void;
}

const TASK_FLOW: TaskState[] = ['CREATED', 'READY', 'LEASED', 'ACTIVE', 'IN_REVIEW', 'DONE'];

/** 状态机可视化：主链路高亮当前态，展示可转移目标。 */
export function StateMachineGraph({ state, allowed, onTransition }: StateMachineGraphProps) {
  const meta = taskStateMeta(state);
  const targetSet = allowed ? new Set(allowed) : null;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5">
        {TASK_FLOW.map((s, index) => {
          const isCurrent = s === state;
          const isReachable = targetSet?.has(s) ?? false;
          return (
            <div key={s} className="flex items-center gap-1.5">
              {index > 0 && <span className="text-ink-faint">→</span>}
              <button
                onClick={isReachable && onTransition ? () => onTransition(s) : undefined}
                className={clsx(
                  'rounded-md border px-2 py-1 text-xs font-medium transition-colors',
                  isCurrent
                    ? 'border-brand bg-brand-soft text-brand'
                    : isReachable
                      ? 'border-ok/40 text-ok hover:bg-ok/10'
                      : 'border-border text-ink-faint',
                  !isReachable && 'cursor-default',
                )}
              >
                {s}
              </button>
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-xs text-ink-muted">
        当前：<span className={clsx('font-semibold', meta.tone === 'ok' ? 'text-ok' : meta.tone === 'danger' ? 'text-danger' : 'text-ink')}>{meta.label}</span>
        {targetSet && <span className="ml-2 text-ink-faint">可迁移：{Array.from(targetSet).join(', ') || '—'}</span>}
      </p>
    </div>
  );
}
