"""事件类型枚举与 payload 模型（**已冻结**）。

来源：``docs/ARCHITECTURE.md`` §10.3 / ``docs/TASK_BREAKDOWN.md`` §12.2。

命名规则
--------
* 全大写下划线，动词一律用**过去式**（``RUN_STARTED`` 而不是 ``RUN_START``）。
* 每种事件必须在 :data:`EVENT_PAYLOAD_MAP` 注册 payload 模型，
  ``event_service``（WP7）写入前强制校验。
* **新增事件只能由 WP1 owner 追加在所属分组段尾**，不得插入中间。

分组与数量
----------
=====================  ====
分组                   数量
=====================  ====
Run                     10
Task                     9
Lease                    6
Loop / Step              9
LLM / Tool               6
Memory                   6
Governance               8
Verify / VCS             8
Worker / System         10
**合计**              **72**
=====================  ====

.. note::
   ``ARCHITECTURE.md`` §10.3 的标题写「62 种」，但其分组明细共 72 项
   （前 8 组恰好 62 项，第 9 组 Worker/System 的 10 项是后补的）。
   本模块以**分组明细为准**实现 72 项，属于文档标题的算术笔误，
   已在 WP1 交付报告中提请架构师订正标题数字。
   :data:`EVENT_TYPE_COUNT` 与 :data:`EVENT_GROUPS` 供 CI 校验使用。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import (
    LeaseState,
    MemoryLayer,
    PolicyDecision,
    RoleName,
    RunState,
    TaskState,
)

__all__ = [
    "EventType",
    "BaseEventPayload",
    "EVENT_PAYLOAD_MAP",
    "EVENT_GROUPS",
    "EVENT_TYPE_COUNT",
    "payload_model_for",
    "validate_payload",
    # payload 模型
    "RunLifecyclePayload",
    "RunFailedPayload",
    "RunRecoveredPayload",
    "MilestoneReachedPayload",
    "TaskLifecyclePayload",
    "TaskFailedPayload",
    "TaskDeferredPayload",
    "LeaseEventPayload",
    "LeaseReclaimedPayload",
    "LoopEventPayload",
    "StepIntentPayload",
    "StepConfirmedPayload",
    "StepFailedPayload",
    "StepReconciledPayload",
    "NoProgressPayload",
    "ActionLoopPayload",
    "LlmCallPayload",
    "LlmCallFailedPayload",
    "ProviderFailoverPayload",
    "ToolCallPayload",
    "ToolCallFailedPayload",
    "ToolOutputTruncatedPayload",
    "MemoryAppendedPayload",
    "MemoryCompressedPayload",
    "MemoryRecalledPayload",
    "PinnedConstraintPayload",
    "PinnedQuotaExceededPayload",
    "ConstraintViolationPayload",
    "PolicyDecisionPayload",
    "ApprovalPayload",
    "BudgetPayload",
    "VerifyStartedPayload",
    "VerifyResultPayload",
    "CommitCreatedPayload",
    "BranchPushedPayload",
    "PullRequestPayload",
    "CiStatusPayload",
    "WorkerPayload",
    "WorkerFencedOutPayload",
    "WatchdogScanPayload",
    "LoopStuckPayload",
    "AlertPayload",
    "EvolutionItemPayload",
]


class EventType(StrEnum):
    """全量事件类型（72 项，分 9 组，段内顺序冻结）。"""

    # ---------------- Run（10）----------------
    RUN_CREATED = "RUN_CREATED"
    RUN_STARTED = "RUN_STARTED"
    RUN_PAUSED = "RUN_PAUSED"
    RUN_RESUMED = "RUN_RESUMED"
    RUN_CANCELLED = "RUN_CANCELLED"
    RUN_SUCCEEDED = "RUN_SUCCEEDED"
    RUN_FAILED = "RUN_FAILED"
    RUN_RECOVERED = "RUN_RECOVERED"
    MILESTONE_REACHED = "MILESTONE_REACHED"
    RUN_ARCHIVED = "RUN_ARCHIVED"

    # ---------------- Task（9）----------------
    TASK_CREATED = "TASK_CREATED"
    TASK_READY = "TASK_READY"
    TASK_LEASED = "TASK_LEASED"
    TASK_ACTIVATED = "TASK_ACTIVATED"
    TASK_BLOCKED = "TASK_BLOCKED"
    TASK_IN_REVIEW = "TASK_IN_REVIEW"
    TASK_DONE = "TASK_DONE"
    TASK_FAILED = "TASK_FAILED"
    TASK_DEFERRED_PATH_CONFLICT = "TASK_DEFERRED_PATH_CONFLICT"

    # ---------------- Lease（6）----------------
    LEASE_ACQUIRED = "LEASE_ACQUIRED"
    LEASE_HEARTBEAT = "LEASE_HEARTBEAT"
    LEASE_EXTENDED = "LEASE_EXTENDED"
    LEASE_RELEASED = "LEASE_RELEASED"
    LEASE_RECLAIMED = "LEASE_RECLAIMED"
    LEASE_REVOKED = "LEASE_REVOKED"

    # ---------------- Loop / Step（9）----------------
    LOOP_STARTED = "LOOP_STARTED"
    LOOP_FINISHED = "LOOP_FINISHED"
    STEP_INTENT_RECORDED = "STEP_INTENT_RECORDED"
    STEP_CONFIRMED = "STEP_CONFIRMED"
    STEP_FAILED = "STEP_FAILED"
    STEP_RECONCILED = "STEP_RECONCILED"
    STEP_UNCERTAIN = "STEP_UNCERTAIN"
    NO_PROGRESS_DETECTED = "NO_PROGRESS_DETECTED"
    ACTION_LOOP_DETECTED = "ACTION_LOOP_DETECTED"

    # ---------------- LLM / Tool（6）----------------
    LLM_CALL = "LLM_CALL"
    LLM_CALL_FAILED = "LLM_CALL_FAILED"
    PROVIDER_FAILOVER = "PROVIDER_FAILOVER"
    TOOL_CALL = "TOOL_CALL"
    TOOL_CALL_FAILED = "TOOL_CALL_FAILED"
    TOOL_OUTPUT_TRUNCATED = "TOOL_OUTPUT_TRUNCATED"

    # ---------------- Memory（6）----------------
    MEMORY_APPENDED = "MEMORY_APPENDED"
    MEMORY_COMPRESSED = "MEMORY_COMPRESSED"
    MEMORY_RECALLED = "MEMORY_RECALLED"
    PINNED_CONSTRAINT_ADDED = "PINNED_CONSTRAINT_ADDED"
    PINNED_QUOTA_EXCEEDED = "PINNED_QUOTA_EXCEEDED"
    CONSTRAINT_VIOLATION = "CONSTRAINT_VIOLATION"

    # ---------------- Governance（8）----------------
    POLICY_ALLOWED = "POLICY_ALLOWED"
    POLICY_DENIED = "POLICY_DENIED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_TIMEOUT = "APPROVAL_TIMEOUT"
    BUDGET_WARNING = "BUDGET_WARNING"
    BUDGET_HALTED = "BUDGET_HALTED"

    # ---------------- Verify / VCS（8）----------------
    VERIFY_STARTED = "VERIFY_STARTED"
    VERIFY_RESULT = "VERIFY_RESULT"
    VERIFY_FAILED = "VERIFY_FAILED"
    COMMIT_CREATED = "COMMIT_CREATED"
    BRANCH_PUSHED = "BRANCH_PUSHED"
    PR_CREATED = "PR_CREATED"
    PR_UPDATED = "PR_UPDATED"
    CI_STATUS_CHANGED = "CI_STATUS_CHANGED"

    # ---------------- Worker / System（10）----------------
    WORKER_REGISTERED = "WORKER_REGISTERED"
    WORKER_HEARTBEAT = "WORKER_HEARTBEAT"
    WORKER_DRAINING = "WORKER_DRAINING"
    WORKER_STOPPED = "WORKER_STOPPED"
    WORKER_FENCED_OUT = "WORKER_FENCED_OUT"
    WATCHDOG_SCAN = "WATCHDOG_SCAN"
    LOOP_STUCK = "LOOP_STUCK"
    ALERT_FIRED = "ALERT_FIRED"
    ALERT_RESOLVED = "ALERT_RESOLVED"
    EVOLUTION_ITEM_CREATED = "EVOLUTION_ITEM_CREATED"


# ---------------------------------------------------------------------------
# Payload 模型
# ---------------------------------------------------------------------------


class BaseEventPayload(BaseModel):
    """全部事件 payload 的基类。

    * ``extra="forbid"``：写库前强校验，防止拼错字段名悄悄进库。
    * ``frozen=True``：payload 一旦构造即不可变，杜绝发布后被下游改写。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


# ---- Run -------------------------------------------------------------------


class RunLifecyclePayload(BaseEventPayload):
    """Run 生命周期通用 payload。"""

    run_id: str
    state: RunState
    previous_state: RunState | None = None
    reason: str = ""
    actor: str = "system"


class RunFailedPayload(BaseEventPayload):
    """``RUN_FAILED``。"""

    run_id: str
    error_code: str
    message: str = ""
    loop_index: int = 0
    recoverable: bool = False


class RunRecoveredPayload(BaseEventPayload):
    """``RUN_RECOVERED``。"""

    run_id: str
    recovery_count: int
    unconfirmed_steps: int = 0
    strategy: str = "reconcile"
    duration_ms: int = 0


class MilestoneReachedPayload(BaseEventPayload):
    """``MILESTONE_REACHED``。"""

    run_id: str
    milestone_index: int
    summary_id: str | None = None
    tasks_done: int = 0
    tokens_compressed: int = 0


# ---- Task ------------------------------------------------------------------


class TaskLifecyclePayload(BaseEventPayload):
    """Task 生命周期通用 payload。"""

    run_id: str
    task_id: str
    task_key: str = ""
    state: TaskState
    previous_state: TaskState | None = None
    reason: str = ""


class TaskFailedPayload(BaseEventPayload):
    """``TASK_FAILED``。"""

    run_id: str
    task_id: str
    task_key: str = ""
    error_code: str
    message: str = ""
    attempt_count: int = 0
    reclaim_count: int = 0


class TaskDeferredPayload(BaseEventPayload):
    """``TASK_DEFERRED_PATH_CONFLICT``。"""

    run_id: str
    task_id: str
    conflicting_task_id: str
    conflicting_paths: list[str] = Field(default_factory=list)


# ---- Lease -----------------------------------------------------------------


class LeaseEventPayload(BaseEventPayload):
    """租约通用 payload。"""

    lease_id: str
    task_id: str
    worker_id: str
    fence_token: int
    state: LeaseState = LeaseState.ACTIVE
    ttl_seconds: int = 0
    heartbeat_count: int = 0
    reason: str = ""


class LeaseReclaimedPayload(BaseEventPayload):
    """``LEASE_RECLAIMED``。"""

    lease_id: str
    task_id: str
    worker_id: str
    fence_token: int
    expiry_reason: str
    reclaim_count: int = 0
    grace_seconds: int = 0


# ---- Loop / Step -----------------------------------------------------------


class LoopEventPayload(BaseEventPayload):
    """``LOOP_STARTED`` / ``LOOP_FINISHED``。"""

    run_id: str
    task_id: str
    loop_index: int
    phase: str = ""
    duration_ms: int = 0
    outcome: str = ""


class StepIntentPayload(BaseEventPayload):
    """``STEP_INTENT_RECORDED``。"""

    run_id: str
    task_id: str
    step_id: str
    idempotency_key: str
    phase: str
    role: RoleName | None = None
    tool_name: str = ""
    side_effect_class: str = "PURE"


class StepConfirmedPayload(BaseEventPayload):
    """``STEP_CONFIRMED``。"""

    run_id: str
    task_id: str
    step_id: str
    idempotency_key: str
    external_ref: str = ""
    duration_ms: int = 0


class StepFailedPayload(BaseEventPayload):
    """``STEP_FAILED``。"""

    run_id: str
    task_id: str
    step_id: str
    idempotency_key: str
    error_code: str
    message: str = ""
    attempt: int = 1
    retryable: bool = False


class StepReconciledPayload(BaseEventPayload):
    """``STEP_RECONCILED`` / ``STEP_UNCERTAIN``。"""

    run_id: str
    task_id: str
    step_id: str
    idempotency_key: str
    probe_result: str
    decision: str
    external_ref: str = ""


class NoProgressPayload(BaseEventPayload):
    """``NO_PROGRESS_DETECTED``。"""

    run_id: str
    task_id: str
    consecutive_loops: int
    threshold: int
    signature: str = ""


class ActionLoopPayload(BaseEventPayload):
    """``ACTION_LOOP_DETECTED``。"""

    run_id: str
    task_id: str
    window: int
    repeated_action: str
    occurrences: int


# ---- LLM / Tool ------------------------------------------------------------


class LlmCallPayload(BaseEventPayload):
    """``LLM_CALL``。金额一律用字符串十进制，避免 float 精度丢失。"""

    run_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    provider: str
    model: str
    role: RoleName | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: str = "0"
    latency_ms: int = 0
    attempt: int = 1
    finish_reason: str = ""


class LlmCallFailedPayload(BaseEventPayload):
    """``LLM_CALL_FAILED``。"""

    provider: str
    model: str
    error_code: str
    message: str = ""
    attempt: int = 1
    retryable: bool = True
    run_id: str | None = None
    step_id: str | None = None


class ProviderFailoverPayload(BaseEventPayload):
    """``PROVIDER_FAILOVER``。"""

    from_provider: str
    to_provider: str
    reason: str
    error_code: str = ""
    run_id: str | None = None
    step_id: str | None = None


class ToolCallPayload(BaseEventPayload):
    """``TOOL_CALL``。"""

    run_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    tool_name: str
    exit_code: int = 0
    duration_ms: int = 0
    policy_decision: PolicyDecision = PolicyDecision.ALLOW
    bytes_out: int = 0


class ToolCallFailedPayload(BaseEventPayload):
    """``TOOL_CALL_FAILED``。"""

    tool_name: str
    error_code: str
    message: str = ""
    exit_code: int = -1
    run_id: str | None = None
    step_id: str | None = None


class ToolOutputTruncatedPayload(BaseEventPayload):
    """``TOOL_OUTPUT_TRUNCATED``。"""

    tool_name: str
    original_bytes: int
    kept_bytes: int
    step_id: str | None = None


# ---- Memory ----------------------------------------------------------------


class MemoryAppendedPayload(BaseEventPayload):
    """``MEMORY_APPENDED``。"""

    run_id: str
    item_id: str
    layer: MemoryLayer
    tokens: int = 0
    salience: float = 0.0
    task_id: str | None = None


class MemoryCompressedPayload(BaseEventPayload):
    """``MEMORY_COMPRESSED``。"""

    run_id: str
    summary_id: str
    level: int
    source_count: int
    tokens_before: int
    tokens_after: int
    info_loss_score: float = 0.0


class MemoryRecalledPayload(BaseEventPayload):
    """``MEMORY_RECALLED``。"""

    run_id: str
    retriever: str
    query_digest: str
    hits: int
    top_score: float = 0.0
    latency_ms: int = 0


class PinnedConstraintPayload(BaseEventPayload):
    """``PINNED_CONSTRAINT_ADDED``。"""

    run_id: str
    constraint_id: str
    origin: str
    priority: int = 100
    text_digest: str = ""


class PinnedQuotaExceededPayload(BaseEventPayload):
    """``PINNED_QUOTA_EXCEEDED``。"""

    run_id: str
    current_tokens: int
    quota_tokens: int
    evicted_ids: list[str] = Field(default_factory=list)


class ConstraintViolationPayload(BaseEventPayload):
    """``CONSTRAINT_VIOLATION``。"""

    run_id: str
    constraint_id: str
    constraint_text: str
    evidence: str = ""
    severity: str = "WARNING"
    step_id: str | None = None


# ---- Governance ------------------------------------------------------------


class PolicyDecisionPayload(BaseEventPayload):
    """``POLICY_ALLOWED`` / ``POLICY_DENIED``。"""

    run_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    action_kind: str
    decision: PolicyDecision
    rule_id: str = ""
    reason: str = ""
    category: str = ""


class ApprovalPayload(BaseEventPayload):
    """审批四事件通用 payload。"""

    approval_id: str
    run_id: str
    task_id: str | None = None
    step_id: str | None = None
    action_kind: str
    state: str
    decided_by: str = ""
    reason: str = ""
    timeout_action: str = ""


class BudgetPayload(BaseEventPayload):
    """``BUDGET_WARNING`` / ``BUDGET_HALTED``。"""

    budget_id: str
    scope: str
    scope_ref: str
    consumed_usd: str
    limit_usd: str
    ratio: float
    action: str
    run_id: str | None = None


# ---- Verify / VCS ----------------------------------------------------------


class VerifyStartedPayload(BaseEventPayload):
    """``VERIFY_STARTED``。"""

    run_id: str
    task_id: str | None = None
    steps: list[str] = Field(default_factory=list)
    commit_sha: str = ""


class VerifyResultPayload(BaseEventPayload):
    """``VERIFY_RESULT`` / ``VERIFY_FAILED``。"""

    run_id: str
    task_id: str | None = None
    step: str
    passed: bool
    duration_ms: int = 0
    error_code: str = ""
    detail: str = ""


class CommitCreatedPayload(BaseEventPayload):
    """``COMMIT_CREATED``。"""

    run_id: str
    task_id: str
    repo_id: str = ""
    branch: str
    sha: str
    message_subject: str = ""
    files_changed: int = 0
    idempotency_key: str = ""


class BranchPushedPayload(BaseEventPayload):
    """``BRANCH_PUSHED``。"""

    run_id: str
    task_id: str
    repo_id: str = ""
    branch: str
    remote: str = "origin"
    forced: bool = False


class PullRequestPayload(BaseEventPayload):
    """``PR_CREATED`` / ``PR_UPDATED``。"""

    run_id: str
    task_id: str
    repo_id: str = ""
    number: int
    url: str = ""
    head: str = ""
    base: str = ""
    action: str = "created"


class CiStatusPayload(BaseEventPayload):
    """``CI_STATUS_CHANGED``。"""

    run_id: str
    task_id: str | None = None
    repo_id: str = ""
    sha: str
    check_name: str
    status: str
    conclusion: str = ""
    url: str = ""


# ---- Worker / System -------------------------------------------------------


class WorkerPayload(BaseEventPayload):
    """Worker 通用 payload。"""

    worker_id: str
    hostname: str = ""
    pid: int = 0
    state: str = "idle"
    capabilities: list[str] = Field(default_factory=list)
    current_task_id: str | None = None
    reason: str = ""


class WorkerFencedOutPayload(BaseEventPayload):
    """``WORKER_FENCED_OUT``。"""

    worker_id: str
    task_id: str
    lease_id: str
    presented_token: int
    current_token: int


class WatchdogScanPayload(BaseEventPayload):
    """``WATCHDOG_SCAN``。"""

    scanned_leases: int = 0
    expired_leases: int = 0
    reclaimed: int = 0
    stuck_runs: int = 0
    duration_ms: int = 0


class LoopStuckPayload(BaseEventPayload):
    """``LOOP_STUCK``。"""

    run_id: str
    task_id: str | None = None
    loop_index: int = 0
    stuck_seconds: int = 0
    last_event_seq: int = 0


class AlertPayload(BaseEventPayload):
    """``ALERT_FIRED`` / ``ALERT_RESOLVED``。"""

    alert_name: str
    severity: str
    summary: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    run_id: str | None = None


class EvolutionItemPayload(BaseEventPayload):
    """``EVOLUTION_ITEM_CREATED``。"""

    item_id: str
    source: str
    score: float = 0.0
    cluster_id: str = ""
    title: str = ""


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

#: 事件类型 → payload 模型。**覆盖率必须 100%**（CI 校验）。
EVENT_PAYLOAD_MAP: dict[EventType, type[BaseEventPayload]] = {
    # Run（10）
    EventType.RUN_CREATED: RunLifecyclePayload,
    EventType.RUN_STARTED: RunLifecyclePayload,
    EventType.RUN_PAUSED: RunLifecyclePayload,
    EventType.RUN_RESUMED: RunLifecyclePayload,
    EventType.RUN_CANCELLED: RunLifecyclePayload,
    EventType.RUN_SUCCEEDED: RunLifecyclePayload,
    EventType.RUN_FAILED: RunFailedPayload,
    EventType.RUN_RECOVERED: RunRecoveredPayload,
    EventType.MILESTONE_REACHED: MilestoneReachedPayload,
    EventType.RUN_ARCHIVED: RunLifecyclePayload,
    # Task（9）
    EventType.TASK_CREATED: TaskLifecyclePayload,
    EventType.TASK_READY: TaskLifecyclePayload,
    EventType.TASK_LEASED: TaskLifecyclePayload,
    EventType.TASK_ACTIVATED: TaskLifecyclePayload,
    EventType.TASK_BLOCKED: TaskLifecyclePayload,
    EventType.TASK_IN_REVIEW: TaskLifecyclePayload,
    EventType.TASK_DONE: TaskLifecyclePayload,
    EventType.TASK_FAILED: TaskFailedPayload,
    EventType.TASK_DEFERRED_PATH_CONFLICT: TaskDeferredPayload,
    # Lease（6）
    EventType.LEASE_ACQUIRED: LeaseEventPayload,
    EventType.LEASE_HEARTBEAT: LeaseEventPayload,
    EventType.LEASE_EXTENDED: LeaseEventPayload,
    EventType.LEASE_RELEASED: LeaseEventPayload,
    EventType.LEASE_RECLAIMED: LeaseReclaimedPayload,
    EventType.LEASE_REVOKED: LeaseEventPayload,
    # Loop / Step（9）
    EventType.LOOP_STARTED: LoopEventPayload,
    EventType.LOOP_FINISHED: LoopEventPayload,
    EventType.STEP_INTENT_RECORDED: StepIntentPayload,
    EventType.STEP_CONFIRMED: StepConfirmedPayload,
    EventType.STEP_FAILED: StepFailedPayload,
    EventType.STEP_RECONCILED: StepReconciledPayload,
    EventType.STEP_UNCERTAIN: StepReconciledPayload,
    EventType.NO_PROGRESS_DETECTED: NoProgressPayload,
    EventType.ACTION_LOOP_DETECTED: ActionLoopPayload,
    # LLM / Tool（6）
    EventType.LLM_CALL: LlmCallPayload,
    EventType.LLM_CALL_FAILED: LlmCallFailedPayload,
    EventType.PROVIDER_FAILOVER: ProviderFailoverPayload,
    EventType.TOOL_CALL: ToolCallPayload,
    EventType.TOOL_CALL_FAILED: ToolCallFailedPayload,
    EventType.TOOL_OUTPUT_TRUNCATED: ToolOutputTruncatedPayload,
    # Memory（6）
    EventType.MEMORY_APPENDED: MemoryAppendedPayload,
    EventType.MEMORY_COMPRESSED: MemoryCompressedPayload,
    EventType.MEMORY_RECALLED: MemoryRecalledPayload,
    EventType.PINNED_CONSTRAINT_ADDED: PinnedConstraintPayload,
    EventType.PINNED_QUOTA_EXCEEDED: PinnedQuotaExceededPayload,
    EventType.CONSTRAINT_VIOLATION: ConstraintViolationPayload,
    # Governance（8）
    EventType.POLICY_ALLOWED: PolicyDecisionPayload,
    EventType.POLICY_DENIED: PolicyDecisionPayload,
    EventType.APPROVAL_REQUESTED: ApprovalPayload,
    EventType.APPROVAL_GRANTED: ApprovalPayload,
    EventType.APPROVAL_REJECTED: ApprovalPayload,
    EventType.APPROVAL_TIMEOUT: ApprovalPayload,
    EventType.BUDGET_WARNING: BudgetPayload,
    EventType.BUDGET_HALTED: BudgetPayload,
    # Verify / VCS（8）
    EventType.VERIFY_STARTED: VerifyStartedPayload,
    EventType.VERIFY_RESULT: VerifyResultPayload,
    EventType.VERIFY_FAILED: VerifyResultPayload,
    EventType.COMMIT_CREATED: CommitCreatedPayload,
    EventType.BRANCH_PUSHED: BranchPushedPayload,
    EventType.PR_CREATED: PullRequestPayload,
    EventType.PR_UPDATED: PullRequestPayload,
    EventType.CI_STATUS_CHANGED: CiStatusPayload,
    # Worker / System（10）
    EventType.WORKER_REGISTERED: WorkerPayload,
    EventType.WORKER_HEARTBEAT: WorkerPayload,
    EventType.WORKER_DRAINING: WorkerPayload,
    EventType.WORKER_STOPPED: WorkerPayload,
    EventType.WORKER_FENCED_OUT: WorkerFencedOutPayload,
    EventType.WATCHDOG_SCAN: WatchdogScanPayload,
    EventType.LOOP_STUCK: LoopStuckPayload,
    EventType.ALERT_FIRED: AlertPayload,
    EventType.ALERT_RESOLVED: AlertPayload,
    EventType.EVOLUTION_ITEM_CREATED: EvolutionItemPayload,
}

#: 分组 → 成员，供 UI 过滤器与 CI 校验使用。
EVENT_GROUPS: dict[str, tuple[EventType, ...]] = {
    "run": (
        EventType.RUN_CREATED,
        EventType.RUN_STARTED,
        EventType.RUN_PAUSED,
        EventType.RUN_RESUMED,
        EventType.RUN_CANCELLED,
        EventType.RUN_SUCCEEDED,
        EventType.RUN_FAILED,
        EventType.RUN_RECOVERED,
        EventType.MILESTONE_REACHED,
        EventType.RUN_ARCHIVED,
    ),
    "task": (
        EventType.TASK_CREATED,
        EventType.TASK_READY,
        EventType.TASK_LEASED,
        EventType.TASK_ACTIVATED,
        EventType.TASK_BLOCKED,
        EventType.TASK_IN_REVIEW,
        EventType.TASK_DONE,
        EventType.TASK_FAILED,
        EventType.TASK_DEFERRED_PATH_CONFLICT,
    ),
    "lease": (
        EventType.LEASE_ACQUIRED,
        EventType.LEASE_HEARTBEAT,
        EventType.LEASE_EXTENDED,
        EventType.LEASE_RELEASED,
        EventType.LEASE_RECLAIMED,
        EventType.LEASE_REVOKED,
    ),
    "loop_step": (
        EventType.LOOP_STARTED,
        EventType.LOOP_FINISHED,
        EventType.STEP_INTENT_RECORDED,
        EventType.STEP_CONFIRMED,
        EventType.STEP_FAILED,
        EventType.STEP_RECONCILED,
        EventType.STEP_UNCERTAIN,
        EventType.NO_PROGRESS_DETECTED,
        EventType.ACTION_LOOP_DETECTED,
    ),
    "llm_tool": (
        EventType.LLM_CALL,
        EventType.LLM_CALL_FAILED,
        EventType.PROVIDER_FAILOVER,
        EventType.TOOL_CALL,
        EventType.TOOL_CALL_FAILED,
        EventType.TOOL_OUTPUT_TRUNCATED,
    ),
    "memory": (
        EventType.MEMORY_APPENDED,
        EventType.MEMORY_COMPRESSED,
        EventType.MEMORY_RECALLED,
        EventType.PINNED_CONSTRAINT_ADDED,
        EventType.PINNED_QUOTA_EXCEEDED,
        EventType.CONSTRAINT_VIOLATION,
    ),
    "governance": (
        EventType.POLICY_ALLOWED,
        EventType.POLICY_DENIED,
        EventType.APPROVAL_REQUESTED,
        EventType.APPROVAL_GRANTED,
        EventType.APPROVAL_REJECTED,
        EventType.APPROVAL_TIMEOUT,
        EventType.BUDGET_WARNING,
        EventType.BUDGET_HALTED,
    ),
    "verify_vcs": (
        EventType.VERIFY_STARTED,
        EventType.VERIFY_RESULT,
        EventType.VERIFY_FAILED,
        EventType.COMMIT_CREATED,
        EventType.BRANCH_PUSHED,
        EventType.PR_CREATED,
        EventType.PR_UPDATED,
        EventType.CI_STATUS_CHANGED,
    ),
    "worker_system": (
        EventType.WORKER_REGISTERED,
        EventType.WORKER_HEARTBEAT,
        EventType.WORKER_DRAINING,
        EventType.WORKER_STOPPED,
        EventType.WORKER_FENCED_OUT,
        EventType.WATCHDOG_SCAN,
        EventType.LOOP_STUCK,
        EventType.ALERT_FIRED,
        EventType.ALERT_RESOLVED,
        EventType.EVOLUTION_ITEM_CREATED,
    ),
}

#: 事件类型总数。
EVENT_TYPE_COUNT: int = len(EventType)


def payload_model_for(event_type: EventType) -> type[BaseEventPayload]:
    """返回某事件类型对应的 payload 模型类。

    :raises KeyError: 事件类型未注册 payload（CI 会先于运行期拦截该情况）。
    """
    return EVENT_PAYLOAD_MAP[event_type]


def validate_payload(event_type: EventType, payload: Any) -> BaseEventPayload:
    """按事件类型校验并归一化 payload。

    :param event_type: 事件类型。
    :param payload: ``BaseEventPayload`` 实例或可转成 dict 的映射。
    :return: 已校验的 payload 模型实例。
    :raises pydantic.ValidationError: payload 与模型不匹配。
    """
    model = payload_model_for(event_type)
    if isinstance(payload, model):
        return payload
    if isinstance(payload, BaseModel):
        return model.model_validate(payload.model_dump())
    return model.model_validate(payload)
