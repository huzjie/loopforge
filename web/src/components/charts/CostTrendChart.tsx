import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { CostTimePoint } from '@/api/types';
import { formatUsd } from '@/utils/format';

export interface CostTrendChartProps {
  data: CostTimePoint[] | undefined;
  /** 预算阈值参考线（USD）。 */
  budgetLine?: number;
  height?: number;
}

/** 成本时序折线（含预算阈值参考线）。 */
export function CostTrendChart({ data, budgetLine, height = 260 }: CostTrendChartProps) {
  const rows = (data ?? []).map((point) => ({
    ts: new Date(point.ts).toLocaleString(),
    cost: Number(point.cost_usd),
  }));

  return (
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--lf-border)" />
          <XAxis dataKey="ts" tick={{ fontSize: 10, fill: 'var(--lf-ink-faint)' }} />
          <YAxis tick={{ fontSize: 10, fill: 'var(--lf-ink-faint)' }} />
          <Tooltip
            contentStyle={{
              backgroundColor: 'var(--lf-surface)',
              border: '1px solid var(--lf-border)',
              fontSize: 12,
            }}
            formatter={(value: number) => formatUsd(value)}
          />
          <Line
            type="monotone"
            dataKey="cost"
            stroke="var(--lf-brand)"
            strokeWidth={2}
            dot={false}
            name="成本"
          />
          {typeof budgetLine === 'number' && budgetLine > 0 && (
            <ReferenceLine y={budgetLine} stroke="var(--lf-danger)" strokeDasharray="4 4" label="预算" />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
