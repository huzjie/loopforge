"""预算账本 —— 多级 scope 预算的读、写、滚动窗口与原子扣减。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``budget/ledger.py``。

原子扣减依赖 :meth:`BudgetRepository.consume` 的表达式 UPDATE
（``consumed_usd = consumed_usd + :amount``），并发下不会丢更新。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from loopforge.core.clock import get_clock
from loopforge.core.config import BudgetSettings, get_settings
from loopforge.core.enums import BudgetAction, BudgetScope
from loopforge.db.uow import UnitOfWork
from loopforge.models.budget import Budget

__all__ = ["BudgetLedger", "DAY_SCOPE_REF_FORMAT"]

#: DAY scope 的 scope_ref 日期格式（UTC）。
DAY_SCOPE_REF_FORMAT: str = "%Y-%m-%d"


class BudgetLedger:
    """预算账本。

    :param uow: 已进入上下文的 :class:`UnitOfWork`（调用方负责 commit）。
    :param settings: 预算配置；``None`` 用全局配置。
    """

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        settings: BudgetSettings | None = None,
    ) -> None:
        """初始化账本。"""
        self._uow: UnitOfWork = uow
        self._settings: BudgetSettings = settings if settings is not None else get_settings().governance.budget

    # -- 读取 ---------------------------------------------------------------

    async def get(self, scope: BudgetScope, scope_ref: str) -> Budget | None:
        """取一条预算（window=total）。"""
        return await self._uow.budgets.get_for_scope(scope, scope_ref, window="total")

    async def list_active(self) -> list[Budget]:
        """列出生效中的预算。"""
        return await self._uow.budgets.list_active()

    # -- 创建 ---------------------------------------------------------------

    async def ensure(
        self,
        *,
        scope: BudgetScope,
        scope_ref: str,
        limit_usd: Decimal | None = None,
    ) -> Budget:
        """确保存在一条预算；不存在则按配置默认值创建。

        :param scope: 作用域。
        :param scope_ref: 作用域引用。
        :param limit_usd: 自定义上限；``None`` 用配置默认。
        :returns: 已存在的预算或新创建的预算。
        """
        existing = await self.get(scope, scope_ref)
        if existing is not None:
            return existing
        if limit_usd is None:
            if scope is BudgetScope.RUN:
                limit_usd = Decimal(str(self._settings.default_run_limit_usd))
            elif scope is BudgetScope.DAY:
                limit_usd = Decimal(str(self._settings.default_day_limit_usd))
            else:
                limit_usd = Decimal(str(self._settings.default_run_limit_usd * 20))
        return await self._uow.budgets.create(
            scope=scope,
            scope_ref=scope_ref,
            limit_usd=limit_usd,
            window="total",
            warn_at=self._settings.warn_at,
            throttle_at=self._settings.throttle_at,
            halt_at=self._settings.halt_at,
            active=True,
        )

    # -- 原子扣减 -----------------------------------------------------------

    async def consume(
        self,
        budget_id: str,
        amount_usd: Decimal,
        *,
        raise_on_halt: bool = False,
    ) -> tuple[Budget, BudgetAction | None]:
        """原子累加消费（并发安全）。

        :param budget_id: 预算 ID。
        :param amount_usd: 消费金额。
        :param raise_on_halt: 触发 HALT 时是否抛异常。
        :returns: ``(预算, 触发的动作或 None)``。
        """
        return await self._uow.budgets.consume(
            budget_id, amount_usd, raise_on_halt=raise_on_halt
        )

    # -- 滚动窗口 -----------------------------------------------------------

    def day_ref(self, now: datetime | None = None) -> str:
        """当前 DAY scope 的引用（UTC 日期字符串）。"""
        moment = now if now is not None else get_clock().now()
        return moment.strftime(DAY_SCOPE_REF_FORMAT)

    async def roll_daily(self, *, now: datetime | None = None) -> int:
        """滚动日窗口：把昨日 DAY 预算重置并前移窗口起点。

        :param now: 当前时刻。
        :returns: 重置的预算条数。
        """
        moment = now if now is not None else get_clock().now()
        today = moment.strftime(DAY_SCOPE_REF_FORMAT)
        rolled = 0
        for budget in await self.list_active():
            if budget.scope is BudgetScope.DAY and budget.scope_ref != today:
                if await self._uow.budgets.reset_window(budget.id, now=moment):
                    rolled += 1
        return rolled

    def scopes_for_run(self, run_id: str, now: datetime | None = None) -> list[tuple[BudgetScope, str]]:
        """Run 涉及的预算 scope 列表（RUN / DAY / ORG）。

        :param run_id: Run ID。
        :param now: 当前时刻。
        :returns: ``[(scope, scope_ref), ...]``。
        """
        moment = now if now is not None else get_clock().now()
        return [
            (BudgetScope.RUN, run_id),
            (BudgetScope.DAY, moment.strftime(DAY_SCOPE_REF_FORMAT)),
            (BudgetScope.ORG, "default"),
        ]
