"""人工审批闸门 —— ``ApprovalGate``（冻结契约 §11.7）。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``approval/gate.py``。

高危操作命中 ``REQUIRE_APPROVAL`` 时：创建审批单 → 挂起等待 → 人工裁决
或超时处置。超时策略只有两种：``deny``（默认，超时即拒绝）与 ``allow``
（超时放行，必须显式配置并写明理由）——默认拒绝是刻意的：16 天无人值守
的系统，审批超时默认放行等于「延迟执行」。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.clock import get_clock
from loopforge.core.config import GovernanceSettings, get_settings
from loopforge.core.enums import Severity
from loopforge.core.event_types import EventType
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork
from loopforge.governance.policy.engine import PolicyAction, PolicyVerdict
from loopforge.models.policy import Approval

__all__ = ["ApprovalGate", "ApprovalOutcome"]


class ApprovalOutcome(BaseModel):
    """审批等待的结果。"""

    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(description="审批单 ID")
    state: str = Field(description="终态：PENDING/APPROVED/REJECTED/EXPIRED/CANCELLED")
    granted: bool = Field(description="最终是否放行（含超时放行）")
    decided_by: str | None = Field(default=None, description="决策人")
    reason: str = Field(default="", description="决策理由")
    timed_out: bool = Field(default=False, description="是否超时")


class ApprovalGate:
    """人工审批闸门。

    :param session_factory: session 工厂；``None`` 用全局。
    :param settings: 治理配置；``None`` 用全局配置。
    """

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        settings: GovernanceSettings | None = None,
    ) -> None:
        """初始化审批闸门。"""
        self._session_factory: Any = session_factory if session_factory is not None else get_session_factory()
        self._settings: GovernanceSettings = settings if settings is not None else get_settings().governance

    # -- 冻结出口：request --------------------------------------------------

    async def request(
        self,
        *,
        run_id: str,
        task_id: str,
        step_id: str,
        action: PolicyAction,
        verdict: PolicyVerdict,
        timeout_s: int,
    ) -> Approval:
        """创建一张审批单（挂起等待）。

        :param run_id: 所属 Run。
        :param task_id: 所属 Task。
        :param step_id: 所属 Step。
        :param action: 待批准动作。
        :param verdict: 触发审批的策略判定。
        :param timeout_s: 超时秒数。
        :returns: 已落库的 :class:`Approval`。
        """
        async with self._open_uow() as uow:
            timeout_action = self._settings.approval_timeout_action
            if timeout_action not in ("deny", "allow"):
                timeout_action = "deny"
            expires_at = get_clock().now() + timedelta(seconds=max(1, timeout_s))
            approval = await uow.approvals.create(
                run_id=run_id,
                task_id=task_id,
                step_id=step_id,
                action_kind=f"{action.kind}.{action.tool_name or 'tool'}",
                action_payload=action.model_dump(exclude_none=True),
                risk_note=verdict.reason,
                rule_id=verdict.rule_id,
                state="pending",
                timeout_action=timeout_action,
                expires_at=expires_at,
            )
            uow.collect_event(
                EventType.APPROVAL_REQUESTED,
                {
                    "approval_id": approval.id,
                    "run_id": run_id,
                    "task_id": task_id,
                    "step_id": step_id,
                    "action_kind": approval.action_kind,
                    "state": "pending",
                    "timeout_action": timeout_action,
                },
                run_id=run_id,
                task_id=task_id,
                step_id=step_id,
                severity=Severity.WARNING,
            )
            return approval

    # -- 冻结出口：wait -----------------------------------------------------

    async def wait(self, *, approval_id: str, timeout_s: int) -> ApprovalOutcome:
        """等待审批结果（轮询，超时按 ``timeout_action`` 处置）。

        :param approval_id: 审批单 ID。
        :param timeout_s: 最大等待秒数。
        :returns: :class:`ApprovalOutcome`。
        """
        deadline = get_clock().monotonic() + max(0, timeout_s)
        poll_interval = 0.2
        while True:
            outcome = await self._poll_once(approval_id)
            if outcome is not None:
                return outcome
            if get_clock().monotonic() >= deadline:
                return await self._expire(approval_id)
            await asyncio.sleep(poll_interval)

    # -- 冻结出口：decide ---------------------------------------------------

    async def decide(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
        reason: str,
    ) -> Approval:
        """人工裁决一张审批单。

        :param approval_id: 审批单 ID。
        :param approved: 是否批准。
        :param decided_by: 决策人标识。
        :param reason: 决策理由。
        :returns: 更新后的 :class:`Approval`。
        """
        async with self._open_uow() as uow:
            changed = await uow.approvals.decide(
                approval_id,
                approved=approved,
                decided_by=decided_by,
                reason=reason,
            )
            approval = await uow.approvals.get_or_raise(approval_id)
            if changed:
                uow.collect_event(
                    EventType.APPROVAL_GRANTED if approved else EventType.APPROVAL_REJECTED,
                    {
                        "approval_id": approval.id,
                        "run_id": approval.run_id,
                        "task_id": approval.task_id,
                        "step_id": approval.step_id,
                        "action_kind": approval.action_kind,
                        "state": approval.state,
                        "decided_by": decided_by,
                        "reason": reason,
                    },
                    run_id=approval.run_id,
                    task_id=approval.task_id,
                    step_id=approval.step_id,
                    severity=Severity.INFO,
                )
            return approval

    # -- 内部 ---------------------------------------------------------------

    def _open_uow(self) -> UnitOfWork:
        return UnitOfWork(session_factory=self._session_factory)

    async def _poll_once(self, approval_id: str) -> ApprovalOutcome | None:
        """轮询一次：终态返回结果；pending 返回 None。"""
        async with self._open_uow() as uow:
            approval = await uow.approvals.get_or_raise(approval_id)
            if approval.state == "pending":
                return None
            granted = approval.state == "approved" or (
                approval.state == "expired" and approval.timeout_action == "allow"
            )
            return ApprovalOutcome(
                approval_id=approval.id,
                state=approval.state.upper(),
                granted=granted,
                decided_by=approval.decided_by,
                reason=approval.decision_reason or "",
                timed_out=approval.state == "expired",
            )

    async def _expire(self, approval_id: str) -> ApprovalOutcome:
        """超时处置：按 timeout_action 置为 expired 或直接放行。"""
        async with self._open_uow() as uow:
            approval = await uow.approvals.get_or_raise(approval_id)
            timeout_action = approval.timeout_action or "deny"
            if timeout_action == "allow":
                # 超时放行：直接批准。
                await uow.approvals.decide(
                    approval_id,
                    approved=True,
                    decided_by="system:timeout",
                    reason="审批超时，按 timeout_action=allow 放行",
                )
                await uow.refresh(approval)
                granted = True
            else:
                await uow.approvals.decide(
                    approval_id,
                    approved=False,
                    decided_by="system:timeout",
                    reason="审批超时，按默认 deny 拒绝",
                )
                await uow.refresh(approval)
                granted = False
            uow.collect_event(
                EventType.APPROVAL_TIMEOUT,
                {
                    "approval_id": approval.id,
                    "run_id": approval.run_id,
                    "task_id": approval.task_id,
                    "step_id": approval.step_id,
                    "action_kind": approval.action_kind,
                    "state": approval.state,
                    "reason": "timeout",
                    "timeout_action": timeout_action,
                },
                run_id=approval.run_id,
                task_id=approval.task_id,
                step_id=approval.step_id,
                severity=Severity.WARNING,
            )
            return ApprovalOutcome(
                approval_id=approval.id,
                state=approval.state.upper(),
                granted=granted,
                decided_by="system:timeout",
                reason="审批超时",
                timed_out=True,
            )
