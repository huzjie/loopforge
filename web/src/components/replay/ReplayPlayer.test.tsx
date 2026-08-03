import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ReplayPlayer } from './ReplayPlayer';
import type { ReplayFrame } from '@/api/types';

const frames: ReplayFrame[] = [
  {
    index: 0,
    loop_index: 1,
    phase: 'plan',
    role: 'PLANNER',
    event_type: 'LOOP_STARTED',
    summary: '规划阶段：拆解任务',
    payload: { steps: 3 },
    context_snapshot: null,
    ts: '2026-08-03T00:00:00.000Z',
  },
  {
    index: 1,
    loop_index: 1,
    phase: 'act',
    role: 'CODER',
    event_type: 'STEP_CONFIRMED',
    summary: '写入 validators.py 测试',
    payload: { tool: 'write_file', path: 'src/calc/validators.py' },
    context_snapshot: { tokens: 1200 },
    ts: '2026-08-03T00:01:00.000Z',
  },
];

describe('ReplayPlayer', () => {
  it('渲染首帧摘要', () => {
    render(<ReplayPlayer frames={frames} />);
    expect(screen.getByText('规划阶段：拆解任务')).toBeInTheDocument();
  });

  it('加载态不崩溃', () => {
    render(<ReplayPlayer frames={undefined} loading />);
    expect(document.querySelector('.animate-pulse')).not.toBeNull();
  });

  it('空帧渲染提示', () => {
    render(<ReplayPlayer frames={[]} />);
    expect(screen.getByText('暂无回放帧')).toBeInTheDocument();
  });
});
