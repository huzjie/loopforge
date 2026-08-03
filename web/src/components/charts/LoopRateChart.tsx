import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

export interface LoopRatePoint {
  ts: string;
  iterations: number;
  success_rate: number;
}

export interface LoopRateChartProps {
  data: LoopRatePoint[] | undefined;
  height?: number;
}

/** 循环轮次速率与成功率双轴图。 */
export function LoopRateChart({ data, height = 240 }: LoopRateChartProps) {
  const rows = (data ?? []).map((point) => ({
    ts: new Date(point.ts).toLocaleTimeString(),
    轮次: point.iterations,
    成功率: point.success_rate * 100,
  }));

  return (
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer>
        <ComposedChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--lf-border)" />
          <XAxis dataKey="ts" tick={{ fontSize: 10, fill: 'var(--lf-ink-faint)' }} />
          <YAxis yAxisId="left" tick={{ fontSize: 10, fill: 'var(--lf-ink-faint)' }} />
          <YAxis yAxisId="right" orientation="right" unit="%" tick={{ fontSize: 10, fill: 'var(--lf-ink-faint)' }} />
          <Tooltip
            contentStyle={{
              backgroundColor: 'var(--lf-surface)',
              border: '1px solid var(--lf-border)',
              fontSize: 12,
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar yAxisId="left" dataKey="轮次" fill="var(--lf-brand)" />
          <Line yAxisId="right" type="monotone" dataKey="成功率" stroke="var(--lf-ok)" strokeWidth={2} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
