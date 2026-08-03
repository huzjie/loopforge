/**
 * ⚠️ DEV-ONLY mock 数据源。
 *
 * 仅当 `import.meta.env.DEV === true` 且 `VITE_USE_MOCK === 'true'` 时启用
 * （见 client.ts 的 isMockEnabled）。**生产构建永远不会进入 mock 分支**，
 * 所有生产数据一律走 REST API。
 *
 * 用途：让前端在无后端的情况下可开发/演示页面骨架。
 */
import type { ApiEnvelope, CursorPage, EventRead, HealthResponse, ProjectRead, RunDetail, TaskRead } from './types';

export function isMockEnabled(): boolean {
  return import.meta.env.DEV && import.meta.env.VITE_USE_MOCK === 'true';
}

function envelope<T>(data: T): ApiEnvelope<T> {
  return { code: 'OK', data, message: '', trace_id: 'mock-00000000000000000000000000' };
}

function page<T>(items: T[]): CursorPage<T> {
  return { items, next_cursor: null, has_more: false, total: items.length };
}

const now = new Date().toISOString();

const mockRuns: RunDetail[] = [
  {
    id: '01J00000000000000000000001',
    project_id: '01J00000000000000000000000',
    repo_id: null,
    goal: '为示例 Python 库补齐单元测试并修复两个已知缺陷',
    constraints: { no_git_push: false },
    state: 'RUNNING',
    loop_index: 12,
    milestone_index: 1,
    max_loops: 200,
    budget_id: null,
    policy_id: null,
    recovery_count: 0,
    error_code: null,
    started_at: now,
    finished_at: null,
    created_at: now,
    updated_at: now,
    stats: {
      task_total: 5,
      task_done: 2,
      task_failed: 0,
      task_active: 1,
      step_total: 48,
      step_unconfirmed: 3,
      event_count: 512,
      cost_usd: '1.23450000',
      prompt_tokens: 482_100,
      completion_tokens: 92_300,
      pending_approvals: 1,
    },
    config_snapshot: {},
  },
  {
    id: '01J00000000000000000000002',
    project_id: '01J00000000000000000000000',
    repo_id: null,
    goal: '重构 CLI 参数解析，支持 --json 双输出',
    constraints: {},
    state: 'SUCCEEDED',
    loop_index: 36,
    milestone_index: 2,
    max_loops: 200,
    budget_id: null,
    policy_id: null,
    recovery_count: 1,
    error_code: null,
    started_at: now,
    finished_at: now,
    created_at: now,
    updated_at: now,
    stats: {
      task_total: 3,
      task_done: 3,
      task_failed: 0,
      task_active: 0,
      step_total: 120,
      step_unconfirmed: 0,
      event_count: 1430,
      cost_usd: '2.98710000',
      prompt_tokens: 1_202_000,
      completion_tokens: 210_000,
      pending_approvals: 0,
    },
    config_snapshot: {},
  },
];

const mockTasks: TaskRead[] = [
  {
    id: '01J00000000000000000000011',
    run_id: '01J00000000000000000000001',
    repo_id: null,
    task_key: 'T-0001',
    title: '为 validators.py 补单元测试',
    body: '',
    state: 'ACTIVE',
    priority: 100,
    depends_on: [],
    touch_paths: ['src/calc/validators.py'],
    version: 3,
    attempt_count: 1,
    reclaim_count: 0,
    max_attempts: 3,
    max_reclaims: 2,
    current_lease_id: '01J00000000000000000000021',
    fence_token: 1,
    external_issue_number: null,
    branch_name: 'loopforge/T-0001',
    pr_url: null,
    blocked_reason: null,
    result: {},
    created_at: now,
    updated_at: now,
  },
  {
    id: '01J00000000000000000000012',
    run_id: '01J00000000000000000000001',
    repo_id: null,
    task_key: 'T-0002',
    title: '修复 core.py 除零缺陷',
    body: '',
    state: 'DONE',
    priority: 90,
    depends_on: ['01J00000000000000000000011'],
    touch_paths: ['src/calc/core.py'],
    version: 2,
    attempt_count: 1,
    reclaim_count: 0,
    max_attempts: 3,
    max_reclaims: 2,
    current_lease_id: null,
    fence_token: 0,
    external_issue_number: null,
    branch_name: 'loopforge/T-0002',
    pr_url: 'https://github.com/example/calc/pull/7',
    blocked_reason: null,
    result: {},
    created_at: now,
    updated_at: now,
  },
];

const mockEvents: EventRead[] = [
  {
    seq: 512,
    id: '01J00000000000000000000031',
    run_id: '01J00000000000000000000001',
    task_id: '01J00000000000000000000011',
    step_id: null,
    type: 'STEP_CONFIRMED',
    severity: 'INFO',
    payload: { phase: 'act', tool_name: 'run_tests' },
    trace_id: 'mock-trace',
    prev_hash: 'abc',
    hash: 'def',
    created_at: now,
  },
  {
    seq: 511,
    id: '01J00000000000000000000032',
    run_id: '01J00000000000000000000001',
    task_id: '01J00000000000000000000011',
    step_id: null,
    type: 'TOOL_CALL',
    severity: 'INFO',
    payload: { tool_name: 'run_tests', duration_ms: 3200 },
    trace_id: 'mock-trace',
    prev_hash: 'abc',
    hash: 'def',
    created_at: now,
  },
];

const mockProjects: ProjectRead[] = [
  {
    id: '01J00000000000000000000000',
    slug: 'sample-python-lib',
    name: '示例 Python 库',
    description: '用于演示长程自主编程的示例项目',
    settings: {},
    created_at: now,
    updated_at: now,
  },
];

const mockHealth: HealthResponse = {
  status: 'degraded',
  version: '1.0.0',
  checks: { db: true, provider: true, executor: false },
  severity: 'WARNING',
  checked_at: now,
};

const getHandlers: Record<string, (params?: Record<string, unknown>) => unknown> = {
  '/healthz': () => mockHealth,
  '/readyz': () => mockHealth,
  '/runs': () => page(mockRuns),
  '/runs/01J00000000000000000000001': () => mockRuns[0],
  '/runs/01J00000000000000000000002': () => mockRuns[1],
  '/tasks': () => page(mockTasks),
  '/events': () => page(mockEvents),
  '/projects': () => page(mockProjects),
  '/costs/summary': () => ({ today_usd: '0.42000000', week_usd: '3.10000000', total_usd: '12.50000000', rate_usd_per_hour: '0.05200000', budget_consumed_ratio: 0.31 }),
  '/costs/timeseries': () => ({
    items: [
      { ts: now, cost_usd: '0.1', prompt_tokens: 1000, completion_tokens: 200 },
      { ts: now, cost_usd: '0.2', prompt_tokens: 2000, completion_tokens: 400 },
    ],
    next_cursor: null,
    has_more: false,
    total: 2,
  }),
};

export function mockGet<T>(path: string, params?: Record<string, unknown>): T | undefined {
  const key = path.split('?')[0];
  const handler = getHandlers[key];
  if (!handler) return undefined;
  return handler(params) as T;
}

export function mockMutate<T>(_path: string, _body?: unknown): T | undefined {
  // 开发期 mock 不真正写库，统一返回空确认（调用方自行处理）。
  return undefined;
}

export { envelope };
