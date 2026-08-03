import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RunStatusBadge } from './RunStatusBadge';

// 用 vi.mock 打桩 useRuns，让 RunTable 不依赖后端。
vi.mock('@/hooks/useRuns', () => ({
  useRuns: () => ({
    data: {
      pages: [
        {
          items: [
            {
              id: 'r1',
              project_id: 'p1',
              repo_id: null,
              goal: '补齐单元测试',
              constraints: {},
              state: 'RUNNING',
              loop_index: 12,
              milestone_index: 0,
              max_loops: 200,
              budget_id: null,
              policy_id: null,
              recovery_count: 0,
              error_code: null,
              started_at: '2026-08-03T00:00:00.000Z',
              finished_at: null,
              created_at: '2026-08-03T00:00:00.000Z',
              updated_at: '2026-08-03T00:00:00.000Z',
            },
            {
              id: 'r2',
              project_id: 'p1',
              repo_id: null,
              goal: '重构 CLI',
              constraints: {},
              state: 'SUCCEEDED',
              loop_index: 36,
              milestone_index: 1,
              max_loops: 200,
              budget_id: null,
              policy_id: null,
              recovery_count: 1,
              error_code: null,
              started_at: '2026-08-01T00:00:00.000Z',
              finished_at: '2026-08-01T02:00:00.000Z',
              created_at: '2026-08-01T00:00:00.000Z',
              updated_at: '2026-08-01T02:00:00.000Z',
            },
          ],
          next_cursor: null,
          has_more: false,
          total: 2,
        },
      ],
    },
    isLoading: false,
    isFetchingNextPage: false,
    error: null,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
  }),
}));

vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ can: () => false }),
}));

vi.mock('@/i18n', () => ({
  t: (key: string) => key,
}));

import { RunTable } from './RunTable';

describe('RunTable', () => {
  it('渲染两种状态的 Run 行', async () => {
    render(<RunTable />);
    expect(screen.getByText('补齐单元测试')).toBeInTheDocument();
    expect(screen.getByText('重构 CLI')).toBeInTheDocument();
    // 状态徽标文案（中文映射在 format.ts 的 runStateMeta）
    expect(screen.getByText('运行中')).toBeInTheDocument();
    expect(screen.getByText('成功')).toBeInTheDocument();
  });

  it('渲染轮次列 12/200 与 36/200', () => {
    render(<RunTable />);
    expect(screen.getByText('12/200')).toBeInTheDocument();
    expect(screen.getByText('36/200')).toBeInTheDocument();
  });
});

describe('RunStatusBadge', () => {
  it('未知状态回退显示原值', () => {
    render(<RunStatusBadge state="MYSTERY" />);
    expect(screen.getByText('MYSTERY')).toBeInTheDocument();
  });
});
