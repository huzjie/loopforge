import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

export interface TokenUsagePoint {
  ts: string;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface TokenUsageChartProps {
  data: TokenUsagePoint[] | undefined;
  height?: number;
}

/** Token 用量堆叠柱状（输入/输出）。 */
export function TokenUsageChart({ data, height = 240 }: TokenUsageChartProps) {
  const rows = (data ?? []).map((point) => ({
    ts: new Date(point.ts).toLocaleTimeString(),
    输入: point.prompt_tokens,
    输出: point.completion_tokens,
  }));

  return (
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--lf-border)" />
          <XAxis dataKey="ts" tick={{ fontSize: 10, fill: 'var(--lf-ink-faint)' }} />
          <YAxis tick={{ fontSize: 10, fill: 'var(--lf-ink-faint)' }} />
          <Tooltip
            contentStyle={{
              backgroundColor: 'var(--lf-surface)',
              border: '1px solid var(--lf-border)',
              fontSize: 12,
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="输入" stackId="tokens" fill="var(--lf-brand)" />
          <Bar dataKey="输出" stackId="tokens" fill="var(--lf-info)" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
