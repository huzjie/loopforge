import { useState } from 'react';
import { Button } from '@/components/common/Button';
import { JsonViewer } from '@/components/common/JsonViewer';
import { auditApi } from '@/api/endpoints';

export interface TraceExplorerProps {
  /** commit SHA 起点。 */
  commitSha: string;
}

/** commit → issue → 轨迹 → LLM → 命令 → 审批 溯源视图（AC-D6）。 */
export function TraceExplorer({ commitSha }: TraceExplorerProps) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await auditApi.trace(commitSha);
      setData(result as Record<string, unknown>);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs text-ink">{commitSha.slice(0, 12)}</span>
        <Button size="sm" variant="secondary" loading={loading} onClick={() => void load()}>
          追踪
        </Button>
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
      {data && <JsonViewer value={data} defaultDepth={3} maxHeight="480px" />}
    </div>
  );
}
