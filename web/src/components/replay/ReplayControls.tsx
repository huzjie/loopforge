import { useEffect, useRef } from 'react';
import { Button } from '@/components/common/Button';
import { useUiStore } from '@/stores/uiStore';
import { t } from '@/i18n';

export interface ReplayControlsProps {
  index: number;
  total: number;
  onIndexChange: (index: number) => void;
  onExport?: () => void;
}

const SPEEDS = [0.5, 1, 2, 4];

/** 播放控制（播放/暂停/步进/倍速/跳转）。 */
export function ReplayControls({ index, total, onIndexChange, onExport }: ReplayControlsProps) {
  const playing = useUiStore((state) => state.replayPlaying);
  const setPlaying = useUiStore((state) => state.setReplayPlaying);
  const speed = useUiStore((state) => state.replaySpeed);
  const setSpeed = useUiStore((state) => state.setReplaySpeed);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!playing) return;
    timerRef.current = setInterval(() => {
      onIndexChange(index + 1);
    }, 1000 / speed);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [playing, speed, index, onIndexChange]);

  // 播放到末尾自动停止
  useEffect(() => {
    if (playing && index >= total - 1) setPlaying(false);
  }, [index, total, playing, setPlaying]);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" variant="secondary" onClick={() => onIndexChange(0)} disabled={index === 0}>
        ⏮
      </Button>
      <Button
        size="sm"
        variant={playing ? 'secondary' : 'primary'}
        onClick={() => setPlaying(!playing)}
        disabled={total === 0 || index >= total - 1}
      >
        {playing ? '⏸' : '▶'}
      </Button>
      <Button size="sm" variant="secondary" onClick={() => onIndexChange(index + 1)} disabled={index >= total - 1}>
        ⏭
      </Button>

      <div className="flex items-center gap-1">
        {SPEEDS.map((s) => (
          <button
            key={s}
            onClick={() => setSpeed(s)}
            className={
              speed === s
                ? 'rounded-md bg-brand-soft px-1.5 py-0.5 text-xs font-medium text-brand'
                : 'rounded-md px-1.5 py-0.5 text-xs text-ink-muted hover:bg-raised'
            }
          >
            {s}×
          </button>
        ))}
      </div>

      <span className="lf-num text-xs text-ink-muted">
        {index + 1} / {total}
      </span>

      {onExport && (
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onExport}>
          {t('common.download')}
        </Button>
      )}
    </div>
  );
}
