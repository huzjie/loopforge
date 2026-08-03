"""审批域 DTO —— 人工闸门的请求、决策与超时处理。

超时策略（``timeout_action``）只有两种取值：``deny``（默认）与 ``allow``。
默认拒绝是刻意的：一个无人值守 16 天的系统，如果审批超时默认放行，
那么「审批」就退化成了「延迟执行」。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from loopforge.core.enums import Severity
from loopforge.schemas.common import LFModel, ORMModel

__all__ = [
    "APPROVAL_STATES",
    "APPROVAL_TIMEOUT_ACTIONS",
    "APPROVAL_ACTION_KINDS",
    "ApprovalCreate",
    "ApprovalDecisionRequest",
    "ApprovalRead",
    "ApprovalDetail",
    "ApprovalQuery",
    "ApprovalBatchDecision",
    "ApprovalSummary",
]

#: 审批单状态机取值。
APPROVAL_STATES: tuple[str, ...] = ("PENDING", "APPROVED", "REJECTED", "EXPIRED", "CANCELLED")

#: 超时后的处置方式。
APPROVAL_TIMEOUT_ACTIONS: tuple[str, ...] = ("deny", "allow")

#: 常见的高危动作类型（不是白名单，仅作为文档与前端提示）。
APPROVAL_ACTION_KINDS: tuple[str, ...] = (
    "git_force_push",
    "git_protected_write",
    "shell_destructive",
    "network_egress",
    "high_cost_call",
    "secret_access",
    "external_publish",
)


class ApprovalCreate(LFModel):
    """发起一张审批单。"""

    run_id: str = Field(min_length=1, max_length=26, description="所属 Run")
    task_id: str | None = Field(default=None, max_length=26, description="所属 Task")
    step_id: str | None = Field(default=None, max_length=26, description="所属 Step")
    action_kind: str = Field(min_length=1, max_length=64, description="待批准的动作类型")
    action_payload: dict[str, object] = Field(default_factory=dict, description="动作参数")
    risk_note: str = Field(default="", max_length=4_000, description="风险说明")
    rule_id: str | None = Field(default=None, max_length=64, description="触发的策略规则")
    timeout_action: str = Field(default="deny", max_length=16, description="超时处置")
    timeout_seconds: int | None = Field(
        default=None, ge=1, le=30 * 24 * 3600, description="超时秒数，省略用全局默认"
    )
    severity: Severity = Field(default=Severity.WARNING, description="风险级别")

    @field_validator("timeout_action")
    @classmethod
    def _check_timeout_action(cls, value: str) -> str:
        """超时处置必须在白名单内。"""
        normalized = value.strip().lower()
        if normalized not in APPROVAL_TIMEOUT_ACTIONS:
            raise ValueError(
                f"timeout_action 必须是 {APPROVAL_TIMEOUT_ACTIONS} 之一，收到 {value!r}"
            )
        return normalized

    @field_validator("action_kind")
    @classmethod
    def _normalize_kind(cls, value: str) -> str:
        """统一小写。"""
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("action_kind 不能为空")
        return normalized

    @model_validator(mode="after")
    def _require_risk_note_for_allow(self) -> ApprovalCreate:
        """超时放行属于危险配置，必须写明理由。"""
        if self.timeout_action == "allow" and not self.risk_note.strip():
            raise ValueError("timeout_action=allow 时必须填写 risk_note 说明为何可以超时放行")
        return self


class ApprovalDecisionRequest(LFModel):
    """对一张审批单作出决策。"""

    approve: bool = Field(description="True=批准，False=拒绝")
    decided_by: str = Field(min_length=1, max_length=200, description="决策人标识")
    reason: str = Field(default="", max_length=4_000, description="决策理由")

    @model_validator(mode="after")
    def _require_reason_on_reject(self) -> ApprovalDecisionRequest:
        """拒绝必须给理由，否则智能体无法据此调整计划。"""
        if not self.approve and not self.reason.strip():
            raise ValueError("拒绝审批时必须填写 reason")
        return self


class ApprovalRead(ORMModel):
    """审批单读模型。"""

    id: str = Field(description="审批单 ID")
    run_id: str = Field(description="所属 Run")
    task_id: str | None = Field(default=None, description="所属 Task")
    step_id: str | None = Field(default=None, description="所属 Step")
    action_kind: str = Field(description="动作类型")
    risk_note: str = Field(default="", description="风险说明")
    rule_id: str | None = Field(default=None, description="触发规则")
    state: str = Field(default="PENDING", description="状态")
    decided_by: str | None = Field(default=None, description="决策人")
    decision_reason: str | None = Field(default=None, description="决策理由")
    timeout_action: str = Field(default="deny", description="超时处置")
    created_at: datetime = Field(description="创建时间")
    expires_at: datetime | None = Field(default=None, description="过期时间")
    decided_at: datetime | None = Field(default=None, description="决策时间")

    @property
    def is_pending(self) -> bool:
        """是否仍在等待决策。"""
        return self.state == "PENDING"

    @property
    def is_terminal(self) -> bool:
        """是否已进入终态。"""
        return self.state in ("APPROVED", "REJECTED", "EXPIRED", "CANCELLED")

    @property
    def was_granted(self) -> bool:
        """最终是否放行（含超时放行）。"""
        if self.state == "APPROVED":
            return True
        return self.state == "EXPIRED" and self.timeout_action == "allow"

    def seconds_remaining(self, now: datetime) -> int | None:
        """距离过期还有多少秒；无过期时间返回 ``None``，已过期返回 0。"""
        if self.expires_at is None:
            return None
        delta = (self.expires_at - now).total_seconds()
        return int(delta) if delta > 0 else 0


class ApprovalDetail(ApprovalRead):
    """审批单详情（含完整动作参数）。"""

    action_payload: dict[str, object] = Field(default_factory=dict, description="动作参数")


class ApprovalQuery(LFModel):
    """审批单查询条件。"""

    run_id: str | None = Field(default=None, max_length=26, description="按 Run 过滤")
    task_id: str | None = Field(default=None, max_length=26, description="按 Task 过滤")
    state: str | None = Field(default=None, max_length=16, description="按状态过滤")
    action_kind: str | None = Field(default=None, max_length=64, description="按动作类型过滤")
    pending_only: bool = Field(default=False, description="只看待决策的")
    created_after: datetime | None = Field(default=None, description="创建时间下界")

    @field_validator("state")
    @classmethod
    def _check_state(cls, value: str | None) -> str | None:
        """状态必须在白名单内。"""
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in APPROVAL_STATES:
            raise ValueError(f"state 必须是 {APPROVAL_STATES} 之一，收到 {value!r}")
        return normalized


class ApprovalBatchDecision(LFModel):
    """批量决策（运维在积压时一次性处理）。"""

    approval_ids: list[str] = Field(default_factory=list, description="审批单 ID 列表")
    decision: ApprovalDecisionRequest = Field(description="统一决策")

    @model_validator(mode="after")
    def _check_ids(self) -> ApprovalBatchDecision:
        """ID 列表去重且不能为空、不能过长。"""
        if not self.approval_ids:
            raise ValueError("approval_ids 不能为空")
        deduped = list(dict.fromkeys(self.approval_ids))
        if len(deduped) > 200:
            raise ValueError("单次批量决策不得超过 200 条")
        object.__setattr__(self, "approval_ids", deduped)
        return self


class ApprovalSummary(LFModel):
    """审批积压概览。"""

    pending: int = Field(default=0, ge=0, description="待决策")
    approved: int = Field(default=0, ge=0, description="已批准")
    rejected: int = Field(default=0, ge=0, description="已拒绝")
    expired: int = Field(default=0, ge=0, description="已超时")
    oldest_pending_at: datetime | None = Field(default=None, description="最早的待决策创建时间")

    @property
    def total(self) -> int:
        """总数。"""
        return self.pending + self.approved + self.rejected + self.expired
