import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';

export interface StatusSlice {
  name: string;
  value: number;
  color: string;
}

export interface RunStatusPieProps {
  data: StatusSlice[] | undefined;
  height?: number;
}

const FALLBACK_COLOR = 'var(--lf-ink-faint)';

/** Run/Task 状态分布环图。 */
export function RunStatusPie({ data, height = 220 }: RunStatusPieProps) {
  const rows = (data ?? []).filter((slice) => slice.value > 0);

  return (
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer>
        <PieChart>
          <Pie data={rows} dataKey="value" nameKey="name" innerRadius="55%" outerRadius="80%" paddingAngle={2}>
            {rows.map((slice) => (
              <Cell key={slice.name} fill={slice.color || FALLBACK_COLOR} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              backgroundColor: 'var(--lf-surface)',
              border: '1px solid var(--lf-border)',
              fontSize: 12,
            }}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
