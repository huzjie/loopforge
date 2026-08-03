"""治理域仓储：Budget / CostRecord / Policy / PolicyRule / Approval。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from loopforge.core.clock import get_clock
from loopforge.core.enums import BudgetAction, BudgetScope, PolicyDecision
from loopforge.core.errors import BudgetExhaustedError
from loopforge.core.hashing import sha256_hex
from loopforge.core.ids import new_ulid
from loopforge.db.repositories.base import BaseRepository, clamp_limit
from loopforge.models.budget import Budget, CostRecord
from loopforge.models.policy import Approval, Policy, PolicyRule

__all__ = [
    "BudgetRepository",
    "CostRecordRepository",
    "PolicyRepository",
    "ApprovalRepository",
]


class BudgetRepository(BaseRepository[Budget]):
    """预算仓储 —— 消费累加走原子 UPDATE，避免并发下丢更新。"""

    model = Budget

    async def get_for_scope(
        self, scope: BudgetScope, scope_ref: str, *, window: str = "total"
    ) -> Budget | None:
        """按 ``(scope, scope_ref, window)`` 取预算（``uq_budgets_scope_ref_window``）。"""
        result = await self.session.execute(
            select(Budget).where(
                Budget.scope == scope,
                Budget.scope_ref == scope_ref,
                Budget.window == window,
            )
        )
        return result.scalar_one_or_none()

    async def consume(
        self,
        budget_id: str,
        amount_usd: Decimal,
        *,
        raise_on_halt: bool = False,
        now: datetime | None = None,
    ) -> tuple[Budget, BudgetAction | None]:
        """原子累加消费并返回触发的动作。

        用 ``consumed_usd = consumed_usd + :amount`` 的表达式 UPDATE，
        而不是「读出来加完再写回去」，后者在并发下会丢更新。

        :param amount_usd: 本次消费额，负值按 0 处理。
        :param raise_on_halt: 越过 halt 阈值时是否直接抛异常。
        :returns: ``(最新预算对象, 触发的动作或 None)``。
        :raises BudgetExhaustedError: ``raise_on_halt=True`` 且触发 HALT。
        """
        delta = amount_usd if amount_usd > 0 else Decimal("0E-8")
        moment = now if now is not None else get_clock().now()
        await self.session.execute(
            update(Budget)
            .where(Budget.id == budget_id)
            .values(consumed_usd=Budget.consumed_usd + delta, updated_at=moment)
        )
        budget = await self.get_or_raise(budget_id)
        await self.session.refresh(budget)
        action = budget.triggered_action()
        if raise_on_halt and action is BudgetAction.HALT:
            raise BudgetExhaustedError(
                "预算已耗尽，Run 必须停机",
                details={
                    "budget_id": budget_id,
                    "limit_usd": str(budget.limit_usd),
                    "consumed_usd": str(budget.consumed_usd),
                },
            )
        return budget, action

    async def list_active(self, *, limit: int | None = None) -> list[Budget]:
        """列出生效中的预算（命中 ``ix_budgets_active``）。"""
        stmt = (
            select(Budget)
            .where(Budget.active.is_(True))
            .order_by(Budget.window_start.desc())
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def reset_window(
        self, budget_id: str, *, now: datetime | None = None
    ) -> bool:
        """滚动窗口重置：消费清零、窗口起点前移。"""
        moment = now if now is not None else get_clock().now()
        result = await self.session.execute(
            update(Budget)
            .where(Budget.id == budget_id)
            .values(consumed_usd=Decimal("0E-8"), window_start=moment, updated_at=moment)
        )
        return int(result.rowcount or 0) == 1


class CostRecordRepository(BaseRepository[CostRecord]):
    """成本流水仓储 —— 五维归因下钻。"""

    model = CostRecord

    async def total_for_run(self, run_id: str) -> Decimal:
        """某 Run 的总花费。"""
        result = await self.session.execute(
            select(func.coalesce(func.sum(CostRecord.cost_usd), 0)).where(
                CostRecord.run_id == run_id
            )
        )
        return Decimal(str(result.scalar_one() or 0))

    async def breakdown_by_model(self, run_id: str) -> dict[str, Decimal]:
        """按模型维度聚合花费。"""
        result = await self.session.execute(
            select(CostRecord.model, func.coalesce(func.sum(CostRecord.cost_usd), 0))
            .where(CostRecord.run_id == run_id)
            .group_by(CostRecord.model)
        )
        return {str(model): Decimal(str(total or 0)) for model, total in result.all()}

    async def breakdown_by_task(self, run_id: str) -> dict[str, Decimal]:
        """按 Task 维度聚合花费（``task_id`` 为空的归入 ``"__unassigned__"``）。"""
        result = await self.session.execute(
            select(CostRecord.task_id, func.coalesce(func.sum(CostRecord.cost_usd), 0))
            .where(CostRecord.run_id == run_id)
            .group_by(CostRecord.task_id)
        )
        return {
            (task_id or "__unassigned__"): Decimal(str(total or 0))
            for task_id, total in result.all()
        }

    async def list_in_window(
        self,
        *,
        start: datetime,
        end: datetime,
        run_id: str | None = None,
        limit: int | None = None,
    ) -> list[CostRecord]:
        """按时间窗口取流水（命中 ``ix_cost_run_time``）。"""
        stmt = select(CostRecord).where(
            CostRecord.occurred_at >= start, CostRecord.occurred_at < end
        )
        if run_id is not None:
            stmt = stmt.where(CostRecord.run_id == run_id)
        stmt = stmt.order_by(CostRecord.occurred_at.desc()).limit(clamp_limit(limit))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class PolicyRepository(BaseRepository[Policy]):
    """策略仓储 —— 同名策略在任意时刻只允许一条 active。"""

    model = Policy

    async def get_active(self, name: str) -> Policy | None:
        """取某名字当前生效的策略（含规则）。"""
        result = await self.session.execute(
            select(Policy)
            .options(selectinload(Policy.rules))
            .where(Policy.name == name, Policy.active.is_(True))
        )
        return result.scalars().first()

    async def next_version(self, name: str) -> int:
        """算出该策略名的下一个版本号。"""
        result = await self.session.execute(
            select(func.coalesce(func.max(Policy.version), 0)).where(Policy.name == name)
        )
        return int(result.scalar_one() or 0) + 1

    async def publish(
        self,
        *,
        name: str,
        yaml_source: str,
        compiled: dict[str, object] | None = None,
        rules: Sequence[dict[str, object]] | None = None,
        activate: bool = True,
        now: datetime | None = None,
    ) -> Policy:
        """发布一个新版本策略，可选立即激活（激活时自动下线同名旧版本）。

        :param rules: 规则字典序列，键对应
                      :class:`~loopforge.models.policy.PolicyRule` 的字段名。
        :returns: 新建的策略对象（``rules`` 已 flush）。
        """
        moment = now if now is not None else get_clock().now()
        version = await self.next_version(name)
        policy = Policy(
            id=new_ulid(),
            name=name,
            version=version,
            active=False,
            yaml_source=yaml_source,
            compiled=dict(compiled) if compiled else {},
            checksum=sha256_hex(yaml_source),
            created_at=moment,
        )
        self.session.add(policy)
        await self.session.flush()

        for index, raw in enumerate(rules or []):
            self.session.add(
                PolicyRule(
                    id=new_ulid(),
                    policy_id=policy.id,
                    rule_id=str(raw.get("rule_id", f"R-{index:03d}")),
                    category=str(raw.get("category", "tool")),
                    match=dict(raw.get("match", {})),  # type: ignore[arg-type]
                    effect=raw.get("effect", PolicyDecision.ALLOW),  # type: ignore[arg-type]
                    order_index=int(raw.get("order", index)),  # type: ignore[arg-type]
                    reason=str(raw.get("reason", "")),
                )
            )
        await self.session.flush()

        if activate:
            await self.activate(policy.id, now=moment)
        return policy

    async def activate(self, policy_id: str, *, now: datetime | None = None) -> bool:
        """激活指定版本，同时把同名其他版本置为非激活。

        :returns: 是否成功激活。
        """
        moment = now if now is not None else get_clock().now()
        policy = await self.get(policy_id)
        if policy is None:
            return False
        await self.session.execute(
            update(Policy)
            .where(Policy.name == policy.name, Policy.id != policy_id)
            .values(active=False)
        )
        result = await self.session.execute(
            update(Policy)
            .where(Policy.id == policy_id)
            .values(active=True, activated_at=moment)
        )
        return int(result.rowcount or 0) == 1

    async def list_rules(self, policy_id: str) -> list[PolicyRule]:
        """按求值顺序列出策略规则（命中 ``ix_rules_policy_order``）。"""
        result = await self.session.execute(
            select(PolicyRule)
            .where(PolicyRule.policy_id == policy_id)
            .order_by(PolicyRule.order_index)
        )
        return list(result.scalars().all())

    async def verify_checksum(self, policy_id: str) -> bool:
        """校验 ``yaml_source`` 是否与 ``checksum`` 一致（防篡改）。"""
        policy = await self.get(policy_id)
        if policy is None:
            return False
        return policy.checksum == sha256_hex(policy.yaml_source)


class ApprovalRepository(BaseRepository[Approval]):
    """人工审批单仓储。"""

    model = Approval

    async def list_pending(
        self, *, run_id: str | None = None, limit: int | None = None
    ) -> list[Approval]:
        """列出待决审批（命中 ``ix_approvals_state_expires``）。"""
        stmt = select(Approval).where(Approval.state == "pending")
        if run_id is not None:
            stmt = stmt.where(Approval.run_id == run_id)
        stmt = stmt.order_by(Approval.created_at).limit(clamp_limit(limit))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def decide(
        self,
        approval_id: str,
        *,
        approved: bool,
        decided_by: str,
        reason: str = "",
        now: datetime | None = None,
    ) -> bool:
        """做出审批决策（只对 ``pending`` 生效，天然幂等）。

        :returns: 是否确实改变了状态。
        """
        moment = now if now is not None else get_clock().now()
        result = await self.session.execute(
            update(Approval)
            .where(Approval.id == approval_id, Approval.state == "pending")
            .values(
                state="approved" if approved else "rejected",
                decided_by=decided_by,
                decision_reason=reason,
                decided_at=moment,
            )
        )
        return int(result.rowcount or 0) == 1

    async def expire_overdue(self, *, now: datetime | None = None) -> int:
        """把超时未决的审批批量置为 ``expired``。

        :returns: 受影响行数。
        """
        moment = now if now is not None else get_clock().now()
        result = await self.session.execute(
            update(Approval)
            .where(
                Approval.state == "pending",
                Approval.expires_at.is_not(None),
                Approval.expires_at <= moment,
            )
            .values(state="expired", decided_at=moment, decision_reason="timeout")
        )
        return int(result.rowcount or 0)
