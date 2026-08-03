import clsx from 'clsx';
import type { ReplayFrame } from '@/api/types';

export interface ReplayStepListProps {
  frames: ReplayFrame[] | undefined;
  activeIndex: number;
  onSelect: (index: number) => void;
}

/** 步骤导航列表（按 Loop 轮次与阶段分组折叠）。 */
export function ReplayStepList({ frames, activeIndex, onSelect }: ReplayStepListProps) {
  if (!frames || frames.length === 0) {
    return <div className="py-6 text-center text-xs text-ink-faint">暂无帧</div>;
  }

  // 按 loop_index 分组
  const groups = new Map<number, ReplayFrame[]>();
  for (const frame of frames) {
    const list = groups.get(frame.loop_index) ?? [];
    list.push(frame);
    groups.set(frame.loop_index, list);
  }

  return (
    <div className="space-y-3">
      {Array.from(groups.entries()).map(([loopIndex, list]) => (
        <div key={loopIndex}>
          <div className="mb-1 text-xs font-semibold text-ink-muted">Loop #{loopIndex}</div>
          <div className="space-y-0.5">
            {list.map((frame) => (
              <button
                key={frame.index}
                onClick={() => onSelect(frame.index)}
                className={clsx(
                  'flex w-full items-center gap-2 rounded px-2 py-1 text-left font-mono text-xs transition-colors',
                  frame.index === activeIndex ? 'bg-brand-soft text-brand' : 'text-ink-muted hover:bg-raised',
                )}
              >
                <span className="lf-num shrink-0 text-ink-faint">{frame.index}</span>
                <span className="shrink-0">{frame.phase}</span>
                <span className="truncate">{frame.event_type}</span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
