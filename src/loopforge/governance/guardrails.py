"""护栏总装 —— 组合预算 + 策略 + 审批的统一入口。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``guardrails.py``。

调用顺序（一次动作的完整治理链路）:

1. :meth:`check_action` —— 先策略（``PolicyEngine.evaluate``），
   再预算（``BudgetGuard.precheck``）；策略 DENY 或预算 HALT 立即拦截。
2. 若策略判定 ``REQUIRE_APPROVAL`` —— 创建审批单（``ApprovalGate.request``），
   等待裁决；未通过则整体拦截。
3. 放行后调用 :meth:`record_cost`（``BudgetGuard.record``）确认实际花费。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import BudgetAction, PolicyDecision
from loopforge.db.session import get_session_factory
from loopforge.governance.approval.gate import ApprovalGate
from loopforge.governance.budget.guard import BudgetGuard, BudgetVerdict
from loopforge.governance.policy.engine import PolicyAction, PolicyEngine, PolicyVerdict

__all__ = ["Guardrails", "GuardrailVerdict"]


class GuardrailVerdict(BaseModel):
    """一次动作的完整治理判定。"""

    model_config = ConfigDict(extra="forbid")

    allowed: bool = Field(description="是否整体放行")
    policy_verdict: PolicyVerdict | None = Field(default=None, description="策略判定")
    budget_verdict: BudgetVerdict | None = Field(default=None, description="预算判定")
    requires_approval: bool = Field(default=False, description="是否需要审批")
    reason: str = Field(default="", description="人类可读原因")


class Guardrails:
    """治理护栏总装。

    :param policy_engine: 策略引擎。
    :param budget_guard: 预算闸门。
    :param approval_gate: 审批闸门。
    :param session_factory: 备用 session 工厂。
    """

    def __init__(
        self,
        *,
        policy_engine: PolicyEngine | None = None,
        budget_guard: BudgetGuard | None = None,
        approval_gate: ApprovalGate | None = None,
        session_factory: Any | None = None,
    ) -> None:
        """初始化护栏总装。"""
        factory = session_factory if session_factory is not None else get_session_factory()
        self.policy_engine: PolicyEngine = policy_engine if policy_engine is not None else PolicyEngine(session_factory=factory)
        self.budget_guard: BudgetGuard = budget_guard if budget_guard is not None else BudgetGuard(session_factory=factory)
        self.approval_gate: ApprovalGate = approval_gate if approval_gate is not None else ApprovalGate(session_factory=factory)

    async def check_action(
        self,
        *,
        run_id: str,
        task_id: str | None,
        step_id: str | None,
        action: PolicyAction,
        estimated_usd: Decimal = Decimal("0"),
        skip_budget: bool = False,
    ) -> GuardrailVerdict:
        """对一次动作做完整治理判定。

        :param run_id: 所属 Run。
        :param task_id: 所属 Task。
        :param step_id: 所属 Step。
        :param action: 动作。
        :param estimated_usd: 预估成本。
        :param skip_budget: 跳过预算预检（只读工具等）。
        :returns: :class:`GuardrailVerdict`。
        """
        policy_verdict = await self.policy_engine.evaluate(action)
        if policy_verdict.decision is PolicyDecision.DENY:
            return GuardrailVerdict(
                allowed=False,
                policy_verdict=policy_verdict,
                reason=policy_verdict.reason,
            )

        budget_verdict: BudgetVerdict | None = None
        if not skip_budget:
            budget_verdict = await self.budget_guard.precheck(
                run_id=run_id, estimated_usd=estimated_usd
            )
            if not budget_verdict.allowed:
                return GuardrailVerdict(
                    allowed=False,
                    policy_verdict=policy_verdict,
                    budget_verdict=budget_verdict,
                    reason=budget_verdict.reason,
                )

        if policy_verdict.decision is PolicyDecision.REQUIRE_APPROVAL:
            return GuardrailVerdict(
                allowed=False,
                policy_verdict=policy_verdict,
                budget_verdict=budget_verdict,
                requires_approval=True,
                reason="需要人工审批",
            )
        return GuardrailVerdict(
            allowed=True,
            policy_verdict=policy_verdict,
            budget_verdict=budget_verdict,
            reason="治理判定放行",
        )

    async def request_approval(
        self,
        *,
        run_id: str,
        task_id: str,
        step_id: str,
        action: PolicyAction,
        verdict: PolicyVerdict,
        timeout_s: int,
    ) -> Any:
        """创建审批单。

        :param run_id: 所属 Run。
        :param task_id: 所属 Task。
        :param step_id: 所属 Step。
        :param action: 动作。
        :param verdict: 触发审批的策略判定。
        :param timeout_s: 超时秒数。
        :returns: :class:`Approval`。
        """
        return await self.approval_gate.request(
            run_id=run_id,
            task_id=task_id,
            step_id=step_id,
            action=action,
            verdict=verdict,
            timeout_s=timeout_s,
        )

    async def record_cost(
        self,
        *,
        run_id: str,
        task_id: str | None,
        provider: str,
        model: str,
        actual_usd: Decimal,
        usage: Any,
    ) -> None:
        """确认实际成本（LLM 调用后）。

        :param run_id: 所属 Run。
        :param task_id: 所属 Task。
        :param provider: Provider 名。
        :param model: 模型名。
        :param actual_usd: 实际开销。
        :param usage: ``Usage`` DTO。
        """
        await self.budget_guard.record(
            run_id=run_id,
            task_id=task_id,
            provider=provider,
            model=model,
            actual_usd=actual_usd,
            usage=usage,
        )
