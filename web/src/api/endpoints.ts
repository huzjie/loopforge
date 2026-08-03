import { api } from './client';
import type {
  ApiKeyIssued,
  ApiKeyRead,
  ApprovalDetail,
  ApprovalRead,
  ApprovalSummary,
  ArtifactRead,
  AuditLogRead,
  BudgetRead,
  ChainVerifyResult,
  CostBreakdown,
  CostSummary,
  CostTimePoint,
  CursorPage,
  EventRead,
  HealthResponse,
  IdResponse,
  MemoryItemRead,
  OkResponse,
  Page,
  PolicyDetail,
  PolicyEvaluation,
  PolicyRead,
  Principal,
  ProjectDetail,
  ProjectRead,
  ProjectStats,
  ProviderConfigRead,
  ProviderTestResult,
  RepoRead,
  ReplayFrame,
  RunControlAction,
  RunCreate,
  RunDetail,
  RunRead,
  RunTimeline,
  RunUpdate,
  StepDetail,
  StepRead,
  SystemInfo,
  TaskCreate,
  TaskDetail,
  TaskGraph,
  TaskRead,
  TaskStateChange,
  TokenResponse,
  UserRead,
  WorkerRead,
  LeaseRead,
  ReconcileReport,
  ContextPack,
  PinnedConstraintRead,
  MilestoneSummary,
  CheckpointRead,
} from './types';

/**
 * 全部端点的类型化调用函数（与 docs/ARCHITECTURE.md §11 的 141 个端点一一对应）。
 * 返回值是解包后的 data 字段（统一信封已在 client.ts 处理）。
 * 路径一律相对于 /api/v1 基址。
 */

/* ----------------------------- 健康与系统 ----------------------------- */

export const healthApi = {
  healthz: () => api.get<HealthResponse>('/healthz', undefined, { public: true }),
  readyz: () => api.get<HealthResponse>('/readyz', undefined, { public: true }),
  detail: () => api.get<HealthResponse>('/health/detail'),
  systemInfo: () => api.get<SystemInfo>('/system/info'),
  systemConfig: () => api.get<Record<string, unknown>>('/system/config'),
};

/* ----------------------------- 认证与密钥 ----------------------------- */

export const authApi = {
  login: (username: string, password: string) =>
    api.post<TokenResponse>('/auth/login', { username, password }, { public: true }),
  me: () => api.get<Principal>('/auth/me'),
  listApiKeys: () => api.get<Page<ApiKeyRead>>('/auth/api-keys'),
  createApiKey: (name: string, scopes: string[], ttlDays?: number) =>
    api.post<ApiKeyIssued>('/auth/api-keys', { name, scopes, ttl_days: ttlDays }),
  revokeApiKey: (keyId: string) => api.del<OkResponse>(`/auth/api-keys/${keyId}`),
  rotateApiKey: (keyId: string) => api.post<ApiKeyIssued>(`/auth/api-keys/${keyId}/rotate`),
};

/* ----------------------------- 项目与仓库 ----------------------------- */

export const projectApi = {
  list: (params?: Record<string, unknown>) => api.get<CursorPage<ProjectRead>>('/projects', params),
  create: (body: { slug: string; name: string; description?: string; settings?: Record<string, unknown> }) =>
    api.post<IdResponse>('/projects', body),
  get: (id: string) => api.get<ProjectDetail>(`/projects/${id}`),
  update: (id: string, body: Record<string, unknown>) => api.patch<ProjectRead>(`/projects/${id}`, body),
  remove: (id: string) => api.del<OkResponse>(`/projects/${id}`),
  stats: (id: string) => api.get<ProjectStats>(`/projects/${id}/stats`),
  listRepos: (params?: Record<string, unknown>) => api.get<CursorPage<RepoRead>>('/repos', params),
  createRepo: (body: Record<string, unknown>) => api.post<IdResponse>('/repos', body),
  getRepo: (id: string) => api.get<RepoRead>(`/repos/${id}`),
  updateRepo: (id: string, body: Record<string, unknown>) => api.patch<RepoRead>(`/repos/${id}`, body),
  deleteRepo: (id: string) => api.del<OkResponse>(`/repos/${id}`),
  verifyRepoAccess: (id: string) => api.post<OkResponse>(`/repos/${id}/verify-access`),
  syncIssues: (id: string) => api.post<OkResponse>(`/repos/${id}/sync-issues`),
};

/* ----------------------------- Run ----------------------------- */

export const runApi = {
  list: (params?: Record<string, unknown>) => api.get<CursorPage<RunRead>>('/runs', params),
  create: (body: RunCreate) => api.post<IdResponse>('/runs', body),
  get: (id: string) => api.get<RunDetail>(`/runs/${id}`),
  update: (id: string, body: RunUpdate) => api.patch<RunRead>(`/runs/${id}`, body),
  remove: (id: string) => api.del<OkResponse>(`/runs/${id}`),
  control: (id: string, action: RunControlAction) => api.post<OkResponse>(`/runs/${id}/${action.action}`, action),
  start: (id: string) => api.post<OkResponse>(`/runs/${id}/start`),
  pause: (id: string) => api.post<OkResponse>(`/runs/${id}/pause`),
  resume: (id: string) => api.post<OkResponse>(`/runs/${id}/resume`),
  cancel: (id: string, force = false) => api.post<OkResponse>(`/runs/${id}/cancel`, { force }),
  retry: (id: string) => api.post<OkResponse>(`/runs/${id}/retry`),
  rollback: (id: string) => api.post<OkResponse>(`/runs/${id}/rollback`),
  timeline: (id: string) => api.get<RunTimeline>(`/runs/${id}/timeline`),
  logs: (id: string, params?: Record<string, unknown>) => api.get<CursorPage<Record<string, unknown>>>(`/runs/${id}/logs`, params),
  cost: (id: string) => api.get<CostSummary>(`/runs/${id}/cost`),
  checkpoints: (id: string) => api.get<Page<CheckpointRead>>(`/runs/${id}/checkpoints`),
  restoreCheckpoint: (id: string, cid: string) => api.post<OkResponse>(`/runs/${id}/checkpoints/${cid}/restore`),
  milestones: (id: string) => api.get<Page<MilestoneSummary>>(`/runs/${id}/milestones`),
  taskGraph: (id: string) => api.get<TaskGraph>(`/runs/${id}/task-graph`),
};

/* ----------------------------- Task ----------------------------- */

export const taskApi = {
  list: (params?: Record<string, unknown>) => api.get<CursorPage<TaskRead>>('/tasks', params),
  create: (body: TaskCreate) => api.post<IdResponse>('/tasks', body),
  batch: (body: { run_id: string; tasks: TaskCreate[] }) => api.post<{ created: number }>('/tasks/batch', body),
  get: (id: string) => api.get<TaskDetail>(`/tasks/${id}`),
  update: (id: string, body: Record<string, unknown>) => api.patch<TaskRead>(`/tasks/${id}`, body),
  remove: (id: string) => api.del<OkResponse>(`/tasks/${id}`),
  transition: (id: string, body: TaskStateChange) => api.post<OkResponse>(`/tasks/${id}/transition`, body),
  lease: (id: string, body: Record<string, unknown>) => api.post<LeaseRead>(`/tasks/${id}/lease`, body),
  heartbeat: (id: string, body: Record<string, unknown>) => api.post<OkResponse>(`/tasks/${id}/heartbeat`, body),
  extendLease: (id: string, body: Record<string, unknown>) => api.post<LeaseRead>(`/tasks/${id}/extend-lease`, body),
  release: (id: string, body: Record<string, unknown>) => api.post<OkResponse>(`/tasks/${id}/release`, body),
  requeue: (id: string) => api.post<OkResponse>(`/tasks/${id}/requeue`),
  steps: (id: string, params?: Record<string, unknown>) => api.get<CursorPage<StepRead>>(`/tasks/${id}/steps`, params),
  dependencies: (id: string) => api.get<{ depends_on: TaskRead[]; depended_by: TaskRead[] }>(`/tasks/${id}/dependencies`),
};

/* ----------------------------- Step / 事件 / 产物 ----------------------------- */

export const stepApi = {
  list: (params?: Record<string, unknown>) => api.get<CursorPage<StepRead>>('/steps', params),
  get: (id: string) => api.get<StepDetail>(`/steps/${id}`),
  llmCalls: (id: string) => api.get<CursorPage<Record<string, unknown>>>(`/steps/${id}/llm-calls`),
  toolCalls: (id: string) => api.get<CursorPage<Record<string, unknown>>>(`/steps/${id}/tool-calls`),
  reconcile: (id: string) => api.post<OkResponse>(`/steps/${id}/reconcile`),
};

export const eventApi = {
  list: (params?: Record<string, unknown>) => api.get<CursorPage<EventRead>>('/events', params),
  get: (id: string) => api.get<EventRead>(`/events/${id}`),
  types: () => api.get<Record<string, Record<string, unknown>>>(`/events/types`),
  export: (body: Record<string, unknown>) => api.post<{ url: string }>('/events/export', body),
};

export const artifactApi = {
  list: (params?: Record<string, unknown>) => api.get<CursorPage<ArtifactRead>>('/artifacts', params),
  create: (body: Record<string, unknown>) => api.post<IdResponse>('/artifacts', body),
  get: (id: string) => api.get<ArtifactRead>(`/artifacts/${id}`),
  download: (id: string) => api.get<{ url: string; expires_at: string | null }>(`/artifacts/${id}/download`),
  remove: (id: string) => api.del<OkResponse>(`/artifacts/${id}`),
};

/* ----------------------------- 记忆与上下文 ----------------------------- */

export const memoryApi = {
  list: (runId: string, params?: Record<string, unknown>) => api.get<CursorPage<MemoryItemRead>>(`/runs/${runId}/memory`, params),
  search: (runId: string, body: { query: string; layers?: string[]; top_k?: number }) =>
    api.post<{ hits: { item: MemoryItemRead; score: number }[]; total_tokens: number; truncated: boolean }>(
      `/runs/${runId}/memory/search`,
      body,
    ),
  contextPack: (runId: string) => api.get<ContextPack>(`/runs/${runId}/memory/context-pack`),
  pinned: (runId: string) => api.get<Page<PinnedConstraintRead>>(`/runs/${runId}/memory/pinned`),
  createPinned: (runId: string, body: { text_content: string; priority?: number; origin?: string }) =>
    api.post<IdResponse>(`/runs/${runId}/memory/pinned`, body),
  deletePinned: (runId: string, cid: string) => api.del<OkResponse>(`/runs/${runId}/memory/pinned/${cid}`),
  compress: (runId: string) => api.post<OkResponse>(`/runs/${runId}/memory/compress`),
};

/* ----------------------------- 预算与成本 ----------------------------- */

export const budgetApi = {
  list: (params?: Record<string, unknown>) => api.get<Page<BudgetRead>>('/budgets', params),
  create: (body: Record<string, unknown>) => api.post<IdResponse>('/budgets', body),
  get: (id: string) => api.get<BudgetRead>(`/budgets/${id}`),
  update: (id: string, body: Record<string, unknown>) => api.patch<BudgetRead>(`/budgets/${id}`, body),
  remove: (id: string) => api.del<OkResponse>(`/budgets/${id}`),
  resetWindow: (id: string) => api.post<OkResponse>(`/budgets/${id}/reset-window`),
};

export const costApi = {
  summary: () => api.get<CostSummary>('/costs/summary'),
  timeseries: (params?: Record<string, unknown>) => api.get<CursorPage<CostTimePoint>>('/costs/timeseries', params),
  breakdown: (params?: Record<string, unknown>) => api.get<CostBreakdown>('/costs/breakdown', params),
  estimate: (body: Record<string, unknown>) => api.post<{ estimated_usd: string }>('/costs/estimate', body),
  reconcile: () => api.post<OkResponse>('/costs/reconcile'),
};

/* ----------------------------- 策略与审批 ----------------------------- */

export const policyApi = {
  list: (params?: Record<string, unknown>) => api.get<Page<PolicyRead>>('/policies', params),
  create: (body: { name: string; yaml_source?: string; rules?: unknown[] }) => api.post<IdResponse>('/policies', body),
  get: (id: string) => api.get<PolicyDetail>(`/policies/${id}`),
  update: (id: string, body: Record<string, unknown>) => api.patch<PolicyRead>(`/policies/${id}`, body),
  remove: (id: string) => api.del<OkResponse>(`/policies/${id}`),
  activate: (id: string) => api.post<OkResponse>(`/policies/${id}/activate`),
  validate: (body: { name: string; yaml_source: string }) => api.post<{ valid: boolean; errors: string[] }>('/policies/validate', body),
  evaluate: (body: Record<string, unknown>) => api.post<PolicyEvaluation>('/policies/evaluate', body),
  redlines: () => api.get<Page<Record<string, unknown>>>('/policies/redlines'),
  testRedlines: () => api.post<{ recall: number; total: number; passed: number }>('/policies/redlines/test'),
};

export const approvalApi = {
  list: (params?: Record<string, unknown>) => api.get<CursorPage<ApprovalRead>>('/approvals', params),
  get: (id: string) => api.get<ApprovalDetail>(`/approvals/${id}`),
  approve: (id: string, body: { decided_by: string; reason?: string }) =>
    api.post<OkResponse>(`/approvals/${id}/approve`, body),
  reject: (id: string, body: { decided_by: string; reason?: string }) =>
    api.post<OkResponse>(`/approvals/${id}/reject`, body),
  summary: () => api.get<ApprovalSummary>('/approvals/summary'),
};

/* ----------------------------- Provider 与角色 ----------------------------- */

export const providerApi = {
  list: () => api.get<Page<ProviderConfigRead>>('/providers'),
  create: (body: Record<string, unknown>) => api.post<IdResponse>('/providers', body),
  get: (id: string) => api.get<ProviderConfigRead>(`/providers/${id}`),
  update: (id: string, body: Record<string, unknown>) => api.patch<ProviderConfigRead>(`/providers/${id}`, body),
  remove: (id: string) => api.del<OkResponse>(`/providers/${id}`),
  test: (id: string, body?: Record<string, unknown>) => api.post<ProviderTestResult>(`/providers/${id}/test`, body),
  models: (id: string) => api.get<{ models: string[] }>(`/providers/${id}/models`),
  pricing: () => api.get<Record<string, unknown>>('/providers/pricing'),
  updatePricing: (body: Record<string, unknown>) => api.put<OkResponse>('/providers/pricing', body),
  roles: () => api.get<Page<Record<string, unknown>>>('/roles'),
  updateRole: (name: string, body: Record<string, unknown>) => api.patch<Record<string, unknown>>(`/roles/${name}`, body),
  testRole: (name: string, body?: Record<string, unknown>) => api.post<Record<string, unknown>>(`/roles/${name}/test`, body),
};

/* ----------------------------- Worker 与租约 ----------------------------- */

export const workerApi = {
  list: (params?: Record<string, unknown>) => api.get<Page<WorkerRead>>('/workers', params),
  get: (id: string) => api.get<WorkerRead>(`/workers/${id}`),
  drain: (id: string, body: { reason?: string; force?: boolean }) => api.post<OkResponse>(`/workers/${id}/drain`, body),
  remove: (id: string) => api.del<OkResponse>(`/workers/${id}`),
  listLeases: (params?: Record<string, unknown>) => api.get<Page<LeaseRead>>('/leases', params),
  expiredLeases: () => api.get<Page<LeaseRead>>('/leases/expired'),
  revokeLease: (id: string) => api.post<OkResponse>(`/leases/${id}/revoke`),
  reclaim: () => api.post<ReconcileReport>('/leases/reclaim'),
};

/* ----------------------------- 回放 / 审计 / 演化 / Webhook ----------------------------- */

export const replayApi = {
  frames: (runId: string, params?: Record<string, unknown>) => api.get<CursorPage<ReplayFrame>>(`/replay/${runId}/frames`, params),
  frame: (runId: string, index: number) => api.get<ReplayFrame>(`/replay/${runId}/frames/${index}`),
  export: (runId: string) => api.post<{ url: string }>(`/replay/${runId}/export`),
};

export const auditApi = {
  logs: (params?: Record<string, unknown>) => api.get<CursorPage<AuditLogRead>>('/audit/logs', params),
  trace: (commitSha: string) => api.get<Record<string, unknown>>(`/audit/trace/${commitSha}`),
  verifyChain: (body?: Record<string, unknown>) => api.post<ChainVerifyResult>('/audit/verify-chain', body),
  export: (body: Record<string, unknown>) => api.post<{ url: string }>('/audit/export', body),
};

export const evolutionApi = {
  list: (params?: Record<string, unknown>) => api.get<CursorPage<Record<string, unknown>>>('/evolution/items', params),
  create: (body: Record<string, unknown>) => api.post<IdResponse>('/evolution/items', body),
  promote: (id: string) => api.post<OkResponse>(`/evolution/items/${id}/promote`),
  reject: (id: string) => api.post<OkResponse>(`/evolution/items/${id}/reject`),
  ingest: (body: Record<string, unknown>) => api.post<OkResponse>('/evolution/ingest', body),
};

export const webhookApi = {
  github: (body: Record<string, unknown>) => api.post<OkResponse>('/webhooks/github', body, { public: true }),
  deliveries: (params?: Record<string, unknown>) => api.get<Page<Record<string, unknown>>>('/webhooks/deliveries', params),
  redeliver: (id: string) => api.post<OkResponse>(`/webhooks/deliveries/${id}/redeliver`),
};

/* ----------------------------- 汇总导出 ----------------------------- */

export const userApi = {
  list: (params?: Record<string, unknown>) => api.get<Page<UserRead>>('/users', params),
  create: (body: { username: string; password: string; role?: string }) => api.post<IdResponse>('/users', body),
  update: (id: string, body: Record<string, unknown>) => api.patch<UserRead>(`/users/${id}`, body),
  remove: (id: string) => api.del<OkResponse>(`/users/${id}`),
};
