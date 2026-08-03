import { useState } from 'react';
import clsx from 'clsx';
import type { ReplayFrame } from '@/api/types';
import { JsonViewer } from '@/components/common/JsonViewer';
import { Badge, type BadgeTone } from '@/components/common/Badge';
import { ReplayControls } from './ReplayControls';

export interface ReplayPlayerProps {
  frames: ReplayFrame[] | undefined;
  loading?: boolean;
  onExport?: () => void;
}

const frameTone: Record<string, BadgeTone> = {
  plan: 'info',
  act: 'warn',
  observe: 'info',
  verify: 'ok',
  commit: 'ok',
};

/** 轨迹回放器主体（帧渲染：思考/工具/结果/上下文）。 */
export function ReplayPlayer({ frames, loading, onExport }: ReplayPlayerProps) {
  const [index, setIndex] = useState(0);
  const total = frames?.length ?? 0;
  const frame = frames?.[index];

  if (loading && !frames) {
    return <div className="h-64 animate-pulse rounded-md bg-raised" />;
  }

  if (!frames || frames.length === 0) {
    return <div className="py-12 text-center text-sm text-ink-muted">暂无回放帧</div>;
  }

  return (
    <div className="space-y-3">
      <ReplayControls
        index={index}
        total={total}
        onIndexChange={setIndex}
        onExport={onExport}
      />

      {frame && (
        <div className="rounded-md border border-border bg-surface">
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
            <span className="lf-num text-xs text-ink-faint">#{frame.index}</span>
            <Badge tone={frameTone[frame.phase] ?? 'muted'}>{frame.phase}</Badge>
            <Badge tone="info">L{frame.loop_index}</Badge>
            <span className="font-mono text-xs text-ink">{frame.event_type}</span>
            <span className="ml-auto text-xs text-ink-faint">{new Date(frame.ts).toLocaleString()}</span>
          </div>
          <div className="space-y-3 p-3">
            <p className={clsx('text-sm', frame.event_type.includes('FAILED') ? 'text-danger' : 'text-ink')}>
              {frame.summary}
            </p>
            {Object.keys(frame.payload).length > 0 && (
              <div>
                <h4 className="mb-1 text-xs font-semibold text-ink-muted">payload</h4>
                <JsonViewer value={frame.payload} defaultDepth={2} />
              </div>
            )}
            {frame.context_snapshot && Object.keys(frame.context_snapshot).length > 0 && (
              <div>
                <h4 className="mb-1 text-xs font-semibold text-ink-muted">context snapshot</h4>
                <JsonViewer value={frame.context_snapshot} defaultDepth={1} maxHeight="200px" />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
