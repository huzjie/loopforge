/**
 * TanStack Query key 工厂 —— Query key 的唯一来源。
 * 铁律：禁止在组件里写字符串字面量 key，否则缓存失效逻辑会散架。
 */
export const queryKeys = {
  health: ['health'] as const,
  systemInfo: ['system', 'info'] as const,

  runs: {
    all: ['runs'] as const,
    list: (params?: Record<string, unknown>) => ['runs', 'list', params ?? {}] as const,
    detail: (id: string) => ['runs', 'detail', id] as const,
    timeline: (id: string) => ['runs', 'timeline', id] as const,
    cost: (id: string) => ['runs', 'cost', id] as const,
    taskGraph: (id: string) => ['runs', 'task-graph', id] as const,
    checkpoints: (id: string) => ['runs', 'checkpoints', id] as const,
  },

  tasks: {
    all: ['tasks'] as const,
    list: (params?: Record<string, unknown>) => ['tasks', 'list', params ?? {}] as const,
    detail: (id: string) => ['tasks', 'detail', id] as const,
    steps: (id: string) => ['tasks', 'steps', id] as const,
  },

  events: {
    all: ['events'] as const,
    list: (params?: Record<string, unknown>) => ['events', 'list', params ?? {}] as const,
    types: ['events', 'types'] as const,
  },

  projects: {
    all: ['projects'] as const,
    list: (params?: Record<string, unknown>) => ['projects', 'list', params ?? {}] as const,
    detail: (id: string) => ['projects', 'detail', id] as const,
  },

  providers: {
    all: ['providers'] as const,
    list: ['providers', 'list'] as const,
    roles: ['providers', 'roles'] as const,
  },

  budgets: {
    all: ['budgets'] as const,
    list: (params?: Record<string, unknown>) => ['budgets', 'list', params ?? {}] as const,
  },

  costs: {
    summary: ['costs', 'summary'] as const,
    timeseries: (params?: Record<string, unknown>) => ['costs', 'timeseries', params ?? {}] as const,
    breakdown: (params?: Record<string, unknown>) => ['costs', 'breakdown', params ?? {}] as const,
  },

  policies: {
    all: ['policies'] as const,
    list: (params?: Record<string, unknown>) => ['policies', 'list', params ?? {}] as const,
    detail: (id: string) => ['policies', 'detail', id] as const,
  },

  approvals: {
    all: ['approvals'] as const,
    list: (params?: Record<string, unknown>) => ['approvals', 'list', params ?? {}] as const,
    summary: ['approvals', 'summary'] as const,
  },

  workers: {
    all: ['workers'] as const,
    list: (params?: Record<string, unknown>) => ['workers', 'list', params ?? {}] as const,
  },

  replay: {
    frames: (runId: string, params?: Record<string, unknown>) => ['replay', runId, 'frames', params ?? {}] as const,
  },

  memory: {
    contextPack: (runId: string) => ['memory', runId, 'context-pack'] as const,
    pinned: (runId: string) => ['memory', runId, 'pinned'] as const,
  },
} as const;
