import clsx from 'clsx';
import type { EventRead } from '@/api/types';
import { severityMeta } from '@/utils/format';

const EVENT_TONE: Record<string, string> = {
  RUN_SUCCEEDED: 'text-ok',
  TASK_DONE: 'text-ok',
  RUN_FAILED: 'text-danger',
  TASK_FAILED: 'text-danger',
  STEP_FAILED: 'text-danger',
  POLICY_DENIED: 'text-danger',
  APPROVAL_REQUESTED: 'text-warn',
  BUDGET_WARNING: 'text-warn',
  BUDGET_HALTED: 'text-danger',
  MEMORY_COMPRESSED: 'text-info',
  COMMIT_CREATED: 'text-info',
  PR_CREATED: 'text-info',
};

function summarize(event: EventRead): string {
  const payload = event.payload ?? {};
  switch (event.type) {
    case 'TOOL_CALL':
      return `tool=${String(payload.tool_name ?? '?')}`;
    case 'LLM_CALL':
      return `model=${String(payload.model ?? '?')} tokens=${String(payload.prompt_tokens ?? '?')}→${String(payload.completion_tokens ?? '?')}`;
    case 'STEP_CONFIRMED':
      return `phase=${String(payload.phase ?? '?')}`;
    default:
      return '';
  }
}

/** 单条事件行（类型、严重级配色、摘要）。 */
export function EventRow({ event }: { event: EventRead }) {
  const sev = severityMeta(event.severity);
  const tone = EVENT_TONE[event.type] ?? 'text-ink';

  return (
    <div className="flex items-start gap-2 border-b border-border/40 px-3 py-1.5 text-xs hover:bg-raised/40">
      <span className="lf-num w-14 shrink-0 text-right text-ink-faint">{event.seq}</span>
      <span
        className={clsx(
          'shrink-0 rounded px-1.5 py-0.5 font-mono',
          sev.tone === 'danger'
            ? 'bg-danger/10 text-danger'
            : sev.tone === 'warn'
              ? 'bg-warn/10 text-warn'
              : 'bg-raised text-ink-muted',
        )}
      >
        {event.severity}
      </span>
      <span className={clsx('shrink-0 font-mono font-medium', tone)}>{event.type}</span>
      <span className="min-w-0 flex-1 truncate text-ink-muted">{summarize(event)}</span>
      <span className="shrink-0 text-ink-faint">{new Date(event.created_at).toLocaleTimeString()}</span>
    </div>
  );
}
