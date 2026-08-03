/**
 * LoopForge 前端 DTO 类型。
 *
 * 铁律（见 docs/ARCHITECTURE.md §10.6）：字段名必须与后端 Pydantic DTO
 * **逐字一致**（snake_case），不做驼峰转换。CI 的 scripts/verify_scale.py
 * 会做 OpenAPI 与 TS 类型的键名交叉校验。
 */

/* ----------------------------- 统一信封 ----------------------------- */

/** 统一响应信封。成功 code="OK"，失败 code="LF-<DOMAIN>-<NNNN>"。 */
export interface ApiEnvelope<T> {
  code: string;
  data: T;
  message: string;
  trace_id: string;
}

/** 游标分页响应（data 固定形状）。 */
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
  total: number;
}

/** 偏移分页元信息。 */
export interface PageMeta {
  total: number;
  limit: number;
  offset: number;
}

/** 偏移分页响应。 */
export interface Page<T> {
  items: T[];
  meta: PageMeta;
}

/** 无返回值动作的确认响应。 */
export interface OkResponse {
  ok: boolean;
  message: string;
}

/** 创建类接口的最小响应。 */
export interface IdResponse {
  id: string;
}

/* ----------------------------- 枚举（与 core/enums.py 一致） ----------------------------- */

export type RunState =
  | 'CREATED'
  | 'PLANNING'
  | 'READY'
  | 'RUNNING'
  | 'PAUSING'
  | 'PAUSED'
  | 'RECONCILING'
  | 'RECOVERING'
  | 'AWAITING_APPROVAL'
  | 'HALTED'
  | 'CANCELLING'
  | 'CANCELLED'
  | 'SUCCEEDED'
  | 'FAILED';

export type TaskState =
  | 'CREATED'
  | 'READY'
  | 'LEASED'
  | 'ACTIVE'
  | 'BLOCKED'
  | 'IN_REVIEW'
  | 'DEFERRED'
  | 'DONE'
  | 'FAILED'
  | 'CANCELLED';

export type StepState =
  | 'PENDING'
  | 'INTENT_RECORDED'
  | 'CONFIRMED'
  | 'FAILED'
  | 'ABANDONED'
  | 'UNCERTAIN';

export type LeaseState = 'ACTIVE' | 'RELEASED' | 'EXPIRED' | 'REVOKED';

export type SideEffectClass = 'PURE' | 'IDEMPOTENT' | 'PROBEABLE' | 'UNSAFE';

export type PolicyDecision = 'ALLOW' | 'DENY' | 'REQUIRE_APPROVAL';

export type MemoryLayer = 'PINNED' | 'WORKING' | 'EPISODIC' | 'SEMANTIC' | 'MILESTONE';

export type RoleName = 'PLANNER' | 'CODER' | 'REVIEWER' | 'TESTER' | 'SUMMARIZER';

export type Severity = 'DEBUG' | 'INFO' | 'NOTICE' | 'WARNING' | 'ERROR' | 'CRITICAL';

export type BudgetScope = 'RUN' | 'DAY' | 'ORG';

export type BudgetAction = 'WARN' | 'THROTTLE' | 'HALT';

/* ----------------------------- 健康与系统 ----------------------------- */

export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy';
  version: string;
  checks: Record<string, boolean>;
  severity: Severity;
  checked_at: string | null;
}

export interface VersionResponse {
  name: string;
  version: string;
  api_version: string;
  schema_revision: string;
  python: string;
}

export interface SystemInfo {
  version: string;
  build: string;
  capabilities: string[];
  providers: string[];
}

/* ----------------------------- 认证 ----------------------------- */

export interface UserRead {
  id: string;
  username: string;
  role: string;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserRead;
}

export interface ApiKeyRead {
  id: string;
  prefix: string;
  name: string;
  scopes: string[];
  user_id: string | null;
  expires_at: string | null;
  last_used_at: string | null;
  revoked: boolean;
  created_at: string;
}

export interface ApiKeyIssued {
  key: ApiKeyRead;
  secret: string;
}

export interface Principal {
  subject_id: string;
  subject_type: string;
  username: string | null;
  role: string;
  scopes: string[];
}

/* ----------------------------- 项目与仓库 ----------------------------- */

export interface ProjectRead {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ProjectDetail extends ProjectRead {
  repos: RepoRead[];
  run_count: number;
  active_run_count: number;
}

export interface ProjectStats {
  run_count: number;
  active_run_count: number;
  succeeded_run_count: number;
  failed_run_count: number;
  total_cost_usd: string;
  success_rate: number;
}

export interface RepoRead {
  id: string;
  project_id: string;
  provider: string;
  remote_url: string | null;
  default_branch: string;
  credentials_ref: string | null;
  local_path: string | null;
  labels_config: Record<string, unknown>;
  webhook_enabled: boolean;
  created_at: string;
  updated_at: string;
}

/* ----------------------------- Run ----------------------------- */

export interface RunRead {
  id: string;
  project_id: string;
  repo_id: string | null;
  goal: string;
  constraints: Record<string, unknown>;
  state: RunState;
  loop_index: number;
  milestone_index: number;
  max_loops: number;
  budget_id: string | null;
  policy_id: string | null;
  recovery_count: number;
  error_code: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RunStats {
  task_total: number;
  task_done: number;
  task_failed: number;
  task_active: number;
  step_total: number;
  step_unconfirmed: number;
  event_count: number;
  cost_usd: string;
  prompt_tokens: number;
  completion_tokens: number;
  pending_approvals: number;
}

export interface RunDetail extends RunRead {
  stats: RunStats;
  config_snapshot: Record<string, unknown>;
}

export interface RunCreate {
  project_id: string;
  repo_id?: string | null;
  goal: string;
  constraints?: Record<string, unknown>;
  max_loops?: number;
  budget_usd?: string | null;
  policy_name?: string | null;
  config_overrides?: Record<string, unknown>;
  auto_start?: boolean;
}

export interface RunUpdate {
  goal?: string;
  constraints?: Record<string, unknown>;
  max_loops?: number;
}

export interface RunControlAction {
  action: 'pause' | 'resume' | 'cancel' | 'halt';
  reason?: string;
  force?: boolean;
}

export interface RunQuery {
  project_id?: string;
  state?: RunState;
  active_only?: boolean;
  created_after?: string;
  created_before?: string;
  limit?: number;
  cursor?: string;
}

export interface TimelinePhase {
  phase: string;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  note?: string;
}

export interface RunTimeline {
  run_id: string;
  phases: TimelinePhase[];
  milestones: MilestoneSummary[];
}

export interface MilestoneSummary {
  id: string;
  run_id: string;
  level: number;
  covers_from_seq: number;
  covers_to_seq: number;
  content: string;
  tokens: number;
  source_item_ids: string[];
  created_at: string;
}

export interface CheckpointRead {
  id: string;
  run_id: string;
  seq: number;
  label: string;
  created_at: string;
}

/* ----------------------------- Task ----------------------------- */

export interface TaskRead {
  id: string;
  run_id: string;
  repo_id: string | null;
  task_key: string;
  title: string;
  body: string;
  state: TaskState;
  priority: number;
  depends_on: string[];
  touch_paths: string[];
  version: number;
  attempt_count: number;
  reclaim_count: number;
  max_attempts: number;
  max_reclaims: number;
  current_lease_id: string | null;
  fence_token: number;
  external_issue_number: number | null;
  branch_name: string | null;
  pr_url: string | null;
  blocked_reason: string | null;
  result: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface TaskDetail extends TaskRead {
  step_count: number;
  unconfirmed_step_count: number;
  cost_usd: string;
  lease_worker_id: string | null;
}

export interface TaskCreate {
  run_id: string;
  task_key: string;
  title: string;
  body?: string;
  repo_id?: string | null;
  priority?: number;
  depends_on?: string[];
  touch_paths?: string[];
  max_attempts?: number;
  max_reclaims?: number;
  external_issue_number?: number | null;
}

export interface TaskStateChange {
  from_state: TaskState;
  to_state: TaskState;
  expected_version: number;
  reason?: string;
}

export interface TaskGraphNode {
  id: string;
  task_key: string;
  title: string;
  state: TaskState;
  priority: number;
  depends_on: string[];
}

export interface TaskGraph {
  run_id: string;
  nodes: TaskGraphNode[];
}

/* ----------------------------- Step / LLM / Tool ----------------------------- */

export interface StepRead {
  id: string;
  run_id: string;
  task_id: string | null;
  loop_index: number;
  phase: string;
  role: RoleName | null;
  state: StepState;
  idempotency_key: string;
  side_effect_class: SideEffectClass;
  intent_payload: Record<string, unknown>;
  result_payload: Record<string, unknown>;
  external_ref: string | null;
  error_code: string | null;
  attempt: number;
  fence_token: number;
  intent_at: string | null;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface LlmCallRead {
  id: string;
  step_id: string | null;
  provider: string;
  model: string;
  role: RoleName | null;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cost_usd: string;
  latency_ms: number;
  attempt: number;
  failover_from: string | null;
  finish_reason: string | null;
  prompt_version: string | null;
  error_code: string | null;
  created_at: string;
}

export interface ToolCallRead {
  id: string;
  step_id: string | null;
  tool_name: string;
  args: Record<string, unknown>;
  result: Record<string, unknown>;
  exit_code: number | null;
  duration_ms: number;
  policy_decision: PolicyDecision;
  truncated: boolean;
  bytes_out: number;
  error_code: string | null;
  stderr_excerpt: string | null;
  created_at: string;
}

export interface StepDetail extends StepRead {
  llm_calls: LlmCallRead[];
  tool_calls: ToolCallRead[];
}

/* ----------------------------- 事件 ----------------------------- */

export interface EventRead {
  seq: number;
  id: string;
  run_id: string | null;
  task_id: string | null;
  step_id: string | null;
  type: string;
  severity: Severity;
  payload: Record<string, unknown>;
  trace_id: string | null;
  prev_hash: string | null;
  hash: string;
  created_at: string;
}

export interface EventQuery {
  run_id?: string;
  task_id?: string;
  types?: string[];
  min_severity?: Severity;
  trace_id?: string;
  after_seq?: number;
  before_seq?: number;
  limit?: number;
  cursor?: string;
}

export interface ChainVerifyResult {
  run_id: string;
  total: number;
  valid: boolean;
  broken_at_seq: number | null;
  reason: string;
}

/* ----------------------------- 记忆 ----------------------------- */

export interface MemoryItemRead {
  id: string;
  run_id: string;
  task_id: string | null;
  layer: MemoryLayer;
  content: string;
  tokens: number;
  salience: number;
  compressed_into: string | null;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface PinnedConstraintRead {
  id: string;
  run_id: string;
  text_content: string;
  text_hash: string;
  priority: number;
  origin: string;
  origin_step_id: string | null;
  hit_count: number;
  active: boolean;
  created_at: string;
}

export interface ContextBlock {
  layer: MemoryLayer;
  tokens: number;
  items: MemoryItemRead[];
}

export interface ContextPack {
  run_id: string;
  total_tokens: number;
  blocks: ContextBlock[];
}

/* ----------------------------- 预算与成本 ----------------------------- */

export interface BudgetRead {
  id: string;
  scope: BudgetScope;
  scope_ref: string;
  limit_usd: string;
  window: string;
  warn_at: number;
  throttle_at: number;
  halt_at: number;
  consumed_usd: string;
  window_start: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface CostSummary {
  today_usd: string;
  week_usd: string;
  total_usd: string;
  rate_usd_per_hour: string;
  budget_consumed_ratio: number;
}

export interface CostTimePoint {
  ts: string;
  cost_usd: string;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface CostAggregate {
  key: string;
  cost_usd: string;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface CostBreakdown {
  dimension: string;
  aggregates: CostAggregate[];
}

/* ----------------------------- 策略与审批 ----------------------------- */

export interface PolicyRead {
  id: string;
  name: string;
  version: number;
  active: boolean;
  checksum: string;
  activated_at: string | null;
  created_at: string;
}

export interface PolicyRuleRead {
  id: string;
  policy_id: string;
  rule_id: string;
  category: string;
  match: Record<string, unknown>;
  effect: PolicyDecision;
  order_index: number;
  reason: string;
  severity: Severity;
}

export interface PolicyDetail extends PolicyRead {
  yaml_source: string;
  compiled: Record<string, unknown>;
  rules: PolicyRuleRead[];
}

export interface PolicyEvaluation {
  decision: PolicyDecision;
  rule_id: string | null;
  reason: string;
  severity: Severity;
  policy_id: string | null;
  policy_version: number | null;
  matched_at: string | null;
  requires_approval: boolean;
}

export interface ApprovalRead {
  id: string;
  run_id: string;
  task_id: string | null;
  step_id: string | null;
  action_kind: string;
  risk_note: string;
  rule_id: string | null;
  state: string;
  decided_by: string | null;
  decision_reason: string | null;
  timeout_action: string;
  created_at: string;
  expires_at: string | null;
  decided_at: string | null;
}

export interface ApprovalDetail extends ApprovalRead {
  action_payload: Record<string, unknown>;
}

export interface ApprovalSummary {
  pending: number;
  approved: number;
  rejected: number;
  expired: number;
  oldest_pending_at: string | null;
}

/* ----------------------------- Provider / 角色 ----------------------------- */

export interface ProviderConfigRead {
  id: string;
  name: string;
  kind: string;
  base_url: string;
  has_api_key: boolean;
  models: string[];
  default_model: string | null;
  priority: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProviderTestResult {
  name: string;
  ok: boolean;
  model: string | null;
  latency_ms: number;
  error_code: string | null;
  message: string;
  tested_at: string | null;
}

export interface AgentRoleRead {
  id: string;
  name: RoleName;
  provider: string;
  model: string;
  temperature: number;
  max_tokens: number;
  system_prompt: string;
  tool_allowlist: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

/* ----------------------------- Worker / 租约 ----------------------------- */

export interface WorkerRead {
  id: string;
  hostname: string;
  pid: number;
  version: string;
  capabilities: Record<string, unknown>;
  state: string;
  current_task_id: string | null;
  drain: boolean;
  started_at: string;
  last_heartbeat_at: string;
}

export interface LeaseRead {
  id: string;
  task_id: string;
  worker_id: string;
  state: LeaseState;
  fence_token: number;
  acquired_at: string;
  expires_at: string;
  released_at: string | null;
  renew_count: number;
}

export interface ReconcileReport {
  expired_leases: number;
  dead_workers: number;
  requeued_tasks: number;
  orphan_steps: number;
  started_at: string | null;
  finished_at: string | null;
  notes: string[];
}

/* ----------------------------- 审计 / 回放 ----------------------------- */

export interface AuditLogRead {
  id: string;
  actor: string;
  actor_type: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  trace_id: string | null;
  ip: string | null;
  reason: string | null;
  created_at: string;
}

export interface ReplayFrame {
  index: number;
  loop_index: number;
  phase: string;
  role: RoleName | null;
  event_type: string;
  summary: string;
  payload: Record<string, unknown>;
  context_snapshot: Record<string, unknown> | null;
  ts: string;
}

/* ----------------------------- 产物 ----------------------------- */

export interface ArtifactRead {
  id: string;
  run_id: string;
  task_id: string | null;
  step_id: string | null;
  kind: string;
  storage_uri: string;
  filename: string;
  size_bytes: number;
  mime_type: string;
  meta: Record<string, unknown>;
  created_at: string;
}
