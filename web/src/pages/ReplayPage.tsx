import { useCallback, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Card } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import { ReplayPlayer } from '@/components/replay/ReplayPlayer';
import { ReplayStepList } from '@/components/replay/ReplayStepList';
import { replayApi } from '@/api/endpoints';
import { useToast } from '@/components/common/Toast';
import { t } from '@/i18n';

/** 轨迹回放页。 */
export default function ReplayPage() {
  const { runId } = useParams<{ runId?: string }>();
  const navigate = useNavigate();
  const toast = useToast();
  const [runIdInput, setRunIdInput] = useState(runId ?? '');
  const [frames, setFrames] = useState<Awaited<ReturnType<typeof replayApi.frames>> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);

  const load = useCallback(
    (id: string) => {
      if (!id.trim()) return;
      setLoading(true);
      setError(null);
      replayApi
        .frames(id, { limit: 200 })
        .then((page) => {
          setFrames(page);
          setActiveIndex(0);
          navigate(`/replay/${id}`, { replace: true });
        })
        .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
        .finally(() => setLoading(false));
    },
    [navigate],
  );

  const exportTrace = () => {
    if (!runId) return;
    replayApi
      .export(runId)
      .then((result) => toast.push(`已导出：${result.url}`, 'success'))
      .catch((err: unknown) => toast.push(err instanceof Error ? err.message : String(err), 'error'));
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-ink">{t('nav.replay')}</h1>
      </div>

      <Card padding={false} className="p-3">
        <div className="flex gap-2">
          <input
            className="h-9 flex-1 rounded-md border border-border bg-surface px-2 font-mono text-sm text-ink focus:border-brand focus:outline-none"
            placeholder="输入 Run ID 载入轨迹"
            value={runIdInput}
            onChange={(event) => setRunIdInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') load(runIdInput);
            }}
          />
          <Button variant="primary" loading={loading} onClick={() => load(runIdInput)}>
            {t('common.search')}
          </Button>
        </div>
        {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      </Card>

      {frames && frames.items.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
          <Card title="帧导航">
            <ReplayStepList frames={frames.items} activeIndex={activeIndex} onSelect={setActiveIndex} />
          </Card>
          <ReplayPlayer frames={frames.items} onExport={exportTrace} />
        </div>
      )}
    </div>
  );
}
