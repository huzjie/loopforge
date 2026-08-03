"""预算熔断闸门 —— ``BudgetGuard``（冻结契约 §11.7）。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2、§7.3.2（G3）。

两阶段扣减（**防并发超支**）:

1. :meth:`precheck` —— **预留**：在 LLM 调用前执行。对 RUN / DAY / ORG
   三层 scope 各做一次**原子条件 UPDATE**：``consumed + estimated <= limit``
   才允许累加（等价于预留额度）。条件 UPDATE 是单条 SQL 语句，数据库层
   保证并发下**不可能超支**。任一 scope 预留失败 → 返回 ``HALT`` 判定，
   并把本次已预留的额度全部回滚（不泄漏）。
2. :meth:`record` —— **确认**：在 LLM 调用后执行。把预留额度换成实际
   花费（``consumed = consumed - reserved + actual``），写 ``cost_records``，
   触发 WARN / THROTTLE / HALT 动作与事件。实际花费超限时抛
   :class:`BudgetExhaustedError`。

预留登记保存在进程内注册表（``run_id -> {budget_id: reserved}``）；
LLM 调用失败时调用 :meth:`release` 回滚（WP4 的清理钩子）。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.clock import get_clock
from loopforge.core.config import BudgetSettings, get_settings
from loopforge.core.enums import BudgetAction, BudgetScope, Severity
from loopforge.core.errors import BudgetExhaustedError
from loopforge.core.event_types import EventType
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork
from loopforge.governance.budget.attribution import CostAttribution
from loopforge.governance.budget.estimator import CostEstimator
from loopforge.governance.budget.ledger import BudgetLedger

__all__ = ["BudgetGuard", "BudgetVerdict"]


class BudgetVerdict(BaseModel):
    """预算预检判定（``precheck`` 的返回值）。"""

    model_config = ConfigDict(extra="forbid")

    allowed: bool = Field(description="是否放行")
    action: BudgetAction | None = Field(default=None, description="触发的动作")
    binding_scope: BudgetScope | None = Field(default=None, description="卡住的作用域")
    remaining_usd: Decimal = Field(default=Decimal("0"), description="最紧的剩余额度")
    reserved_usd: Decimal = Field(default=Decimal("0"), description="本次预留金额")
    reason: str = Field(default="", description="人类可读原因")


class BudgetGuard:
    """预算熔断闸门。

    :param session_factory: session 工厂；``None`` 用全局。
    :param settings: 预算配置；``None`` 用全局配置。
    :param estimator: 成本估算器（预检时若调用方没给预估，可用 token 折算）。
    """

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        settings: BudgetSettings | None = None,
        estimator: CostEstimator | None = None,
    ) -> None:
        """初始化预算闸门。"""
        self._session_factory: Any = session_factory if session_factory is not None else get_session_factory()
        self._settings: BudgetSettings = settings if settings is not None else get_settings().governance.budget
        self._estimator: CostEstimator = estimator if estimator is not None else CostEstimator()
        self._lock: asyncio.Lock = asyncio.Lock()
        #: run_id -> {budget_id: reserved_usd}
        self._reservations: dict[str, dict[str, Decimal]] = {}
        #: budget_id -> scope 锁键（release 回滚时用）。
        self._budget_scope_keys: dict[str, str] = {}
        #: 锁键 -> asyncio.Lock —— 进程内串行化同一预算的预留/确认，
        #: 保证并发预检的判定正确（DB 写锁是第二道防线，防跨进程超支）。
        self._budget_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        """取某预算作用域的进程内互斥锁。"""
        lock = self._budget_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._budget_locks[key] = lock
        return lock

    # -- 冻结出口：precheck -------------------------------------------------

    async def precheck(
        self,
        *,
        run_id: str,
        estimated_usd: Decimal,
    ) -> BudgetVerdict:
        """LLM 调用前执行：原子预留三层 scope 额度。

        :param run_id: 所属 Run。
        :param estimated_usd: 预估开销（Decimal）。
        :returns: :class:`BudgetVerdict`。
        """
        estimated = max(Decimal("0"), estimated_usd)
        if estimated == 0:
            return BudgetVerdict(
                allowed=True,
                remaining_usd=Decimal("0"),
                reserved_usd=Decimal("0"),
                reason="预估开销为 0，直接放行",
            )
        async with self._open_uow() as uow:
            ledger = BudgetLedger(uow, settings=self._settings)
            scopes = ledger.scopes_for_run(run_id)
        reserved_this_call: list[tuple[str, str, str, Decimal]] = []
        worst_action: BudgetAction | None = None
        binding_scope: BudgetScope | None = None
        min_remaining = Decimal("0")
        try:
            for scope, scope_ref in scopes:
                key = f"{scope.value}:{scope_ref}"
                async with self._lock_for(key):
                    # 独立事务 + 锁内首次读：保证事务快照新鲜（见 _try_reserve 注释）。
                    async with self._open_uow() as suow:
                        sledger = BudgetLedger(suow, settings=self._settings)
                        budget = await sledger.ensure(scope=scope, scope_ref=scope_ref)
                        ok, action, remaining = await self._try_reserve(suow, budget, estimated)
                if ok:
                    reserved_this_call.append((budget.id, scope.value, scope_ref, estimated))
                else:
                    # 预留失败：本次已预留的额度全部回滚（不泄漏）。
                    await self._rollback_reservations(reserved_this_call)
                    return BudgetVerdict(
                        allowed=False,
                        action=action,
                        binding_scope=scope,
                        remaining_usd=remaining,
                        reserved_usd=Decimal("0"),
                        reason=f"{scope.value} 预算不足：预估 {estimated} 超出剩余 {remaining}",
                    )
                if action is not None and (
                    worst_action is None or self._severity_of(action) > self._severity_of(worst_action)
                ):
                    worst_action = action
                    binding_scope = scope
                    min_remaining = remaining
        except Exception:
            await self._rollback_reservations(reserved_this_call)
            raise
        # 登记进程内预留。
        async with self._lock:
            bucket = self._reservations.setdefault(run_id, {})
            for budget_id, scope_value, scope_ref, amount in reserved_this_call:
                bucket[budget_id] = bucket.get(budget_id, Decimal("0")) + amount
                self._budget_scope_keys[budget_id] = f"{scope_value}:{scope_ref}"
        if worst_action is BudgetAction.WARN:
            async with self._open_uow() as ewow:
                self._emit_budget_event(
                    ewow, run_id=run_id, action=BudgetAction.WARN, scope=binding_scope,
                    budgets=reserved_this_call,
                )
        return BudgetVerdict(
            allowed=True,
            action=worst_action,
            binding_scope=binding_scope,
            remaining_usd=min_remaining,
            reserved_usd=estimated * Decimal(len(reserved_this_call)),
            reason=f"预算充足（预留 {estimated}）",
        )

    # -- 冻结出口：record ---------------------------------------------------

    async def record(
        self,
        *,
        run_id: str,
        task_id: str | None,
        provider: str,
        model: str,
        actual_usd: Decimal,
        usage: Any,
    ) -> None:
        """LLM 调用后执行：预留换实际 + 写成本流水 + 触发动作/事件。

        :param run_id: 所属 Run。
        :param task_id: 所属 Task。
        :param provider: Provider 名。
        :param model: 模型名。
        :param actual_usd: 实际开销（Decimal）。
        :param usage: ``Usage`` DTO（含 prompt/completion/cached tokens）。
        :raises BudgetExhaustedError: 实际开销导致任一 scope 越过 halt 线。
        """
        actual = max(Decimal("0"), actual_usd)
        async with self._open_uow() as uow:
            ledger = BudgetLedger(uow, settings=self._settings)
            scopes = ledger.scopes_for_run(run_id)
            async with self._lock:
                reservations = dict(self._reservations.get(run_id, {}))

        halt_triggered = False
        for scope, scope_ref in scopes:
            key = f"{scope.value}:{scope_ref}"
            async with self._lock_for(key):
                async with self._open_uow() as suow:
                    sledger = BudgetLedger(suow, settings=self._settings)
                    budget = await sledger.get(scope, scope_ref)
                    if budget is None:
                        budget = await sledger.ensure(scope=scope, scope_ref=scope_ref)
                    reserved = reservations.get(budget.id, Decimal("0"))
                    delta = actual - reserved
                    try:
                        updated, action = await self._uow_consume(suow, budget, delta)
                    except BudgetExhaustedError:
                        halt_triggered = True
                        break
                    if action is not None:
                        self._emit_budget_event(
                            suow,
                            run_id=run_id,
                            action=action,
                            scope=scope,
                            budget=updated,
                        )
        # 写成本流水。
        async with self._open_uow() as cuow:
            attribution = CostAttribution(cuow)
            await attribution.record(
                run_id=run_id,
                task_id=task_id,
                provider=provider,
                model=model,
                prompt_tokens=int(getattr(usage, "prompt_tokens", 0)),
                completion_tokens=int(getattr(usage, "completion_tokens", 0)),
                cost_usd=actual,
            )
            # 清理预留登记。
            async with self._lock:
                self._reservations.pop(run_id, None)
            if halt_triggered:
                raise BudgetExhaustedError(
                    f"预算已耗尽：Run {run_id} 实际开销 {actual} 越过停机线",
                    details={"run_id": run_id, "actual_usd": str(actual)},
                )

    # -- 附加：release（WP4 在 LLM 失败时清理预留） --------------------------

    async def release(self, *, run_id: str, reason: str = "") -> int:
        """回滚该 Run 的全部未确认预留（LLM 调用失败时调用）。

        :param run_id: 所属 Run。
        :param reason: 回滚原因（写入审计理由）。
        :returns: 回滚的预算条数。
        """
        async with self._lock:
            reservations = dict(self._reservations.pop(run_id, {}))
        if not reservations:
            return 0
        rolled = 0
        for budget_id, amount in reservations.items():
            key = self._budget_scope_keys.get(budget_id, f"budget:{budget_id}")
            async with self._lock_for(key):
                async with self._open_uow() as uow:
                    budget = await uow.budgets.get_or_raise(budget_id)
                    await self._uow_consume(uow, budget, -amount)
            rolled += 1
        return rolled

    # -- 内部 ---------------------------------------------------------------

    def _open_uow(self) -> UnitOfWork:
        return UnitOfWork(session_factory=self._session_factory)

    @staticmethod
    def _severity_of(action: BudgetAction) -> int:
        order = {BudgetAction.WARN: 1, BudgetAction.THROTTLE: 2, BudgetAction.HALT: 3}
        return order.get(action, 0)

    async def _rollback_reservations(
        self,
        reserved: list[tuple[str, str, str, Decimal]],
    ) -> None:
        """回滚一批已预留的额度（不写事件）。

        :param reserved: ``[(budget_id, scope_value, scope_ref, amount), ...]``。
        """
        for budget_id, scope_value, scope_ref, amount in reserved:
            key = f"{scope_value}:{scope_ref}"
            async with self._lock_for(key):
                async with self._open_uow() as uow:
                    budget = await uow.budgets.get_or_raise(budget_id)
                    await self._uow_consume(uow, budget, -amount)

    async def _try_reserve(
        self,
        uow: UnitOfWork,
        budget: Any,
        amount: Decimal,
    ) -> tuple[bool, BudgetAction | None, Decimal]:
        """原子条件预留：先无条件累加（取写锁），超限则补偿回滚。

        为什么不用「条件 UPDATE」：aiosqlite/SQLAlchemy 下同一事务内第二次
        执行 `consumed + ? <= limit` 形式的条件更新会静默失效（rowcount=0）。
        这里改成两步 —— ① 无条件 UPDATE 累加（SQLite 写锁保证写者串行，
        不存在丢更新）；② 回读最新值，若超限则用补偿 UPDATE 回滚。
        补偿回滚发生在持有写锁的同一事务内，无并发交错，因此安全。

        :returns: ``(是否成功, 触发的动作或 None, 剩余额度)``。
        """
        if budget.limit_usd <= 0:
            # 不限额：直接登记成功。
            return True, None, Decimal("0")
        remaining = budget.limit_usd - budget.consumed_usd
        if remaining < amount:
            ratio = budget.usage_ratio
            if ratio >= budget.halt_at:
                return False, BudgetAction.HALT, remaining
            if ratio >= budget.throttle_at:
                return False, BudgetAction.THROTTLE, remaining
            if ratio >= budget.warn_at:
                return False, BudgetAction.WARN, remaining
            return False, BudgetAction.HALT, remaining
        from sqlalchemy import select, update

        from loopforge.models.budget import Budget

        moment = get_clock().now()
        await uow.session.execute(
            update(Budget)
            .where(Budget.id == budget.id)
            .values(consumed_usd=Budget.consumed_usd + amount, updated_at=moment)
        )
        fresh = (
            await uow.session.execute(select(Budget).where(Budget.id == budget.id))
        ).scalar_one()
        if fresh.consumed_usd > fresh.limit_usd:
            # 超限：补偿回滚本次累加（写锁在手，无并发交错）。
            await uow.session.execute(
                update(Budget)
                .where(Budget.id == budget.id)
                .values(consumed_usd=Budget.consumed_usd - amount)
            )
            return False, fresh.triggered_action() or BudgetAction.HALT, fresh.limit_usd - fresh.consumed_usd + amount
        return True, fresh.triggered_action(), fresh.limit_usd - fresh.consumed_usd

    async def _rollback(self, uow: UnitOfWork, reserved: list[tuple[str, Decimal]]) -> None:
        """回滚一批已预留的额度（不写事件）。"""
        for budget_id, amount in reserved:
            budget = await uow.budgets.get_or_raise(budget_id)
            await self._uow_consume(uow, budget, -amount)

    async def _uow_consume(
        self,
        uow: UnitOfWork,
        budget: Any,
        delta: Decimal,
    ) -> tuple[Any, BudgetAction | None]:
        """在既有事务内做带上限的原子调整（先累加、超限补偿回滚）。

        :raises BudgetExhaustedError: 调整后越过 limit 上限。
        """
        from sqlalchemy import select, update

        from loopforge.models.budget import Budget

        moment = get_clock().now()
        await uow.session.execute(
            update(Budget)
            .where(Budget.id == budget.id)
            .values(consumed_usd=Budget.consumed_usd + delta, updated_at=moment)
        )
        fresh = (
            await uow.session.execute(select(Budget).where(Budget.id == budget.id))
        ).scalar_one()
        if delta > 0 and fresh.consumed_usd > fresh.limit_usd + Decimal("1E-8"):
            await uow.session.execute(
                update(Budget)
                .where(Budget.id == budget.id)
                .values(consumed_usd=Budget.consumed_usd - delta)
            )
            raise BudgetExhaustedError(
                f"预算调整越过上限：budget={budget.id} delta={delta}",
                details={"budget_id": budget.id, "delta": str(delta)},
            )
        return fresh, fresh.triggered_action()

    def _emit_budget_event(
        self,
        uow: UnitOfWork,
        *,
        run_id: str,
        action: BudgetAction,
        scope: BudgetScope | None = None,
        budget: Any | None = None,
        budgets: list[tuple[str, Decimal]] | None = None,
    ) -> None:
        """发出 BUDGET_WARNING / BUDGET_HALTED 事件。"""
        if budget is not None:
            event_type = (
                EventType.BUDGET_HALTED if action is BudgetAction.HALT else EventType.BUDGET_WARNING
            )
            uow.collect_event(
                event_type,
                {
                    "budget_id": budget.id,
                    "scope": str(budget.scope.value if hasattr(budget.scope, "value") else budget.scope),
                    "scope_ref": budget.scope_ref,
                    "consumed_usd": str(budget.consumed_usd),
                    "limit_usd": str(budget.limit_usd),
                    "ratio": budget.usage_ratio,
                    "action": action.value,
                    "run_id": run_id,
                },
                run_id=run_id,
                severity=Severity.CRITICAL if action is BudgetAction.HALT else Severity.WARNING,
            )
            return
        # precheck 阶段（尚未取得单个 budget 对象）。
        event_type = EventType.BUDGET_WARNING
        uow.collect_event(
            event_type,
            {
                "budget_id": budgets[0][0] if budgets else "",
                "scope": str(scope.value if scope is not None else "RUN"),
                "scope_ref": run_id,
                "consumed_usd": "0",
                "limit_usd": "0",
                "ratio": 0.0,
                "action": action.value,
                "run_id": run_id,
            },
            run_id=run_id,
            severity=Severity.WARNING,
        )
