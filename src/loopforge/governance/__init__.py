"""治理层（WP5 · governance 包）。

* ``budget`` —— 预算熔断（多级 scope、三级动作、原子扣减防并发超支）。
* ``policy`` —— 安全策略引擎（deny > approval > allow 严格短路）。
* ``approval`` —— 人工审批闸门。
* ``audit`` —— 不可篡改的审计哈希链。
* :class:`~loopforge.governance.guardrails.Guardrails` —— 统一入口。

冻结出口（``TASK_BREAKDOWN.md`` §11.7）全部从这里可导入：

    from loopforge.governance import (
        PolicyEngine, PolicyAction, PolicyVerdict,
        BudgetGuard, BudgetVerdict,
        ApprovalGate, ApprovalOutcome,
        AuditChain, ChainVerifyReport,
        Guardrails,
    )
"""

from __future__ import annotations

from loopforge.governance.approval.gate import ApprovalGate, ApprovalOutcome
from loopforge.governance.audit.chain import AuditChain, ChainRecord, ChainVerifyReport
from loopforge.governance.budget.guard import BudgetGuard, BudgetVerdict
from loopforge.governance.guardrails import Guardrails, GuardrailVerdict
from loopforge.governance.policy.engine import (
    PolicyAction,
    PolicyEngine,
    PolicyVerdict,
)

__all__ = [
    "ApprovalGate",
    "ApprovalOutcome",
    "AuditChain",
    "BudgetGuard",
    "BudgetVerdict",
    "ChainRecord",
    "ChainVerifyReport",
    "GuardrailVerdict",
    "Guardrails",
    "PolicyAction",
    "PolicyEngine",
    "PolicyVerdict",
]
