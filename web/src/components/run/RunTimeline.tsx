import clsx from 'clsx';
import type { RunTimeline, TimelinePhase } from '@/api/types';
import { formatDuration, formatRelativeTime } from '@/utils/format';
import { EmptyState } from '@/components/common/EmptyState';
import { t } from '@/i18n';

export interface RunTimelineProps {
  data: RunTimeline | undefined;
  loading?: boolean;
}

const phaseTone: Record<string, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  muted: 'bg-border',
};

function phaseStatus(phase: TimelinePhase): 'ok' | 'warn' | 'danger' | 'muted' {
  if (phase.status === 'failed') return 'danger';
  if (phase.status === 'running') return 'warn';
  if (phase.status === 'done') return 'ok';
  return 'muted';
}

/** Run 阶段时间轴（里程碑 + 阶段耗时 + 异常标记）。 */
export function RunTimeline({ data, loading }: RunTimelineProps) {
  if (loading && !data) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-10 animate-pulse rounded-md bg-raised" />
        ))}
      </div>
    );
  }

  if (!data || data.phases.length === 0) {
    return <EmptyState title={t('common.empty')} />;
  }

  return (
    <ol className="relative ml-2 border-l border-border pl-4">
      {data.phases.map((phase, index) => {
        const tone = phaseStatus(phase);
        const duration =
          phase.started_at && phase.finished_at
            ? new Date(phase.finished_at).getTime() - new Date(phase.started_at).getTime()
            : null;
        return (
          <li key={`${phase.phase}-${index}`} className="relative pb-4">
            <span
              className={clsx(
                'absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full ring-2 ring-surface',
                phaseTone[tone],
              )}
            />
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-ink">{phase.phase}</span>
              <span className={clsx('text-xs', tone === 'danger' ? 'text-danger' : 'text-ink-muted')}>
                {phase.status}
              </span>
              {phase.started_at && (
                <span className="text-xs text-ink-faint">{formatRelativeTime(phase.started_at)}</span>
              )}
              {duration !== null && (
                <span className="lf-num text-xs text-ink-faint">{formatDuration(duration)}</span>
              )}
            </div>
            {phase.note && <p className="mt-0.5 text-xs text-ink-muted">{phase.note}</p>}
          </li>
        );
      })}
    </ol>
  );
}
