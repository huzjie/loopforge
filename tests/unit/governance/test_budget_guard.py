"""预算熔断测试 —— 三级动作 / halt 停住 / 并发不超支。

DoD（``TASK_BREAKDOWN`` §7.4）：三级动作在正确阈值触发；``halt`` 后 Run
确实停住；**并发场景 100% 不超支**。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from loopforge.core.config import BudgetSettings
from loopforge.core.enums import BudgetAction, BudgetScope
from loopforge.governance.budget.guard import BudgetGuard, BudgetVerdict
from loopforge.db.uow import UnitOfWork
from loopforge.providers.types import Usage
from tests.factories import make_project, make_run

pytestmark = pytest.mark.asyncio


def _settings(*, run_limit: float = 5.0) -> BudgetSettings:
    return BudgetSettings(
        default_run_limit_usd=run_limit,
        default_day_limit_usd=100.0,
        warn_at=0.5,
        throttle_at=0.8,
        halt_at=1.0,
    )


async def _run_id(uow: object) -> str:
    """建一个真实 Run（cost_records 有 runs 外键）。"""
    project = await make_project(uow)  # type: ignore[arg-type]
    run = await make_run(uow, project_id=project.id)  # type: ignore[arg-type]
    return run.id


async def test_precheck_allows_within_budget(session_factory: object) -> None:
    """预算充足时放行并预留额度（RUN/DAY/ORG 三层各 1.0）。"""
    async with UnitOfWork(session_factory=session_factory) as uow:  # type: ignore[arg-type]
        run_id = await _run_id(uow)
    guard = BudgetGuard(session_factory=session_factory, settings=_settings())
    verdict = await guard.precheck(run_id=run_id, estimated_usd=Decimal("1.0"))
    assert verdict.allowed is True
    assert verdict.reserved_usd == Decimal("3.0")  # 三层 scope 各预留 1.0
    await guard.release(run_id=run_id)


async def test_precheck_halts_when_exceeds(session_factory: object) -> None:
    """预估超限 → HALT，且不泄漏预留。"""
    async with UnitOfWork(session_factory=session_factory) as uow:  # type: ignore[arg-type]
        run_id = await _run_id(uow)
    guard = BudgetGuard(session_factory=session_factory, settings=_settings(run_limit=1.0))
    verdict = await guard.precheck(run_id=run_id, estimated_usd=Decimal("10.0"))
    assert verdict.allowed is False
    assert verdict.action is BudgetAction.HALT
    async with UnitOfWork(session_factory=session_factory) as uow:  # type: ignore[arg-type]
        budget = await uow.budgets.get_for_scope(BudgetScope.RUN, run_id)
        assert budget.consumed_usd == Decimal("0E-8"), "预检失败不得残留预留"


async def test_record_settles_and_writes_cost(session_factory: object) -> None:
    """record 把预留换成实际成本并写 cost_records。"""
    async with UnitOfWork(session_factory=session_factory) as uow:  # type: ignore[arg-type]
        run_id = await _run_id(uow)
    guard = BudgetGuard(session_factory=session_factory, settings=_settings())
    await guard.precheck(run_id=run_id, estimated_usd=Decimal("2.0"))
    usage = Usage(prompt_tokens=1000, completion_tokens=500)
    await guard.record(
        run_id=run_id,
        task_id=None,
        provider="mock",
        model="mock-large",
        actual_usd=Decimal("1.5"),
        usage=usage,
    )
    from datetime import UTC, datetime

    async with UnitOfWork(session_factory=session_factory) as uow:  # type: ignore[arg-type]
        budget = await uow.budgets.get_for_scope(BudgetScope.RUN, run_id)
        assert budget.consumed_usd == Decimal("1.5")
        records = await uow.costs.list_in_window(
            start=datetime(2020, 1, 1, tzinfo=UTC),
            end=datetime(2100, 1, 1, tzinfo=UTC),
            run_id=run_id,
        )
        assert len(records) == 1
        assert records[0].cost_usd == Decimal("1.5")


async def test_threshold_actions_triggered(session_factory: object) -> None:
    """WARN / THROTTLE / HALT 在正确使用率触发。"""
    async with UnitOfWork(session_factory=session_factory) as uow:  # type: ignore[arg-type]
        run_id = await _run_id(uow)
    guard = BudgetGuard(session_factory=session_factory, settings=_settings(run_limit=10.0))
    # 消耗到 6.0 → 使用率 0.6 → WARN（warn_at=0.5）
    v1 = await guard.precheck(run_id=run_id, estimated_usd=Decimal("6.0"))
    assert v1.allowed is True
    assert v1.action is BudgetAction.WARN
    await guard.record(
        run_id=run_id, task_id=None, provider="mock", model="m",
        actual_usd=Decimal("6.0"), usage=Usage(),
    )
    # 再消耗 3.0 → 累计 9.0 → 使用率 0.9 → THROTTLE（throttle_at=0.8）
    v2 = await guard.precheck(run_id=run_id, estimated_usd=Decimal("3.0"))
    assert v2.allowed is True
    assert v2.action is BudgetAction.THROTTLE
    await guard.record(
        run_id=run_id, task_id=None, provider="mock", model="m",
        actual_usd=Decimal("3.0"), usage=Usage(),
    )
    # 再消耗 1.0 → 恰好触顶 10.0 → 放行但动作 HALT（阈值语义）
    v3 = await guard.precheck(run_id=run_id, estimated_usd=Decimal("1.0"))
    assert v3.allowed is True
    assert v3.action is BudgetAction.HALT
    # 之后任何正额预检 → 拦截 HALT（Run 停住）
    v4 = await guard.precheck(run_id=run_id, estimated_usd=Decimal("0.5"))
    assert v4.allowed is False
    assert v4.action is BudgetAction.HALT


async def test_concurrent_precheck_no_overspend(tmp_path: object, event_bus: object) -> None:
    """并发 10 个预检（各 1.0，上限 5.0）→ 恰好 5 个放行，绝不超支。

    用**文件库 + 连接池**（每个 uow 独立连接）模拟真实并发：
    内存 StaticPool 单连接会把并发会话串到同一条 DBAPI 连接上，
    事务互相缠绕，无法验证原子条件更新的正确性。
    """
    from pathlib import Path

    from loopforge.db.base import Base
    from loopforge.db.engine import create_engine_from_settings
    from loopforge.db.session import create_session_factory
    from loopforge.core.config import DbSettings

    db_path = Path(str(tmp_path)) / "concurrent.db"  # type: ignore[arg-type]
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    engine = create_engine_from_settings(
        DbSettings(url=url, pool_size=10, max_overflow=5, pool_pre_ping=False, echo=False),
        url=url,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    try:
        async with UnitOfWork(session_factory=factory) as uow:
            project = await make_project(uow)
            run = await make_run(uow, project_id=project.id)
            run_id = run.id
            from loopforge.governance.budget.ledger import BudgetLedger

            ledger = BudgetLedger(uow, settings=_settings(run_limit=5.0))
            for scope, scope_ref in ledger.scopes_for_run(run_id):
                await ledger.ensure(scope=scope, scope_ref=scope_ref)
        guard = BudgetGuard(session_factory=factory, settings=_settings(run_limit=5.0))
        results = await asyncio.gather(
            *[guard.precheck(run_id=run_id, estimated_usd=Decimal("1.0")) for _ in range(10)],
            return_exceptions=True,
        )
        allowed = [r for r in results if isinstance(r, BudgetVerdict) and r.allowed]
        blocked = [r for r in results if isinstance(r, BudgetVerdict) and not r.allowed]
        assert len(allowed) == 5, f"放行 {len(allowed)} 个"
        assert len(blocked) == 5, f"拦截 {len(blocked)} 个"
        # 放行的确认扣减。
        for verdict in allowed:
            await guard.record(
                run_id=run_id, task_id=None, provider="mock", model="m",
                actual_usd=Decimal("1.0"), usage=Usage(),
            )
        async with UnitOfWork(session_factory=factory) as uow:
            budget = await uow.budgets.get_for_scope(BudgetScope.RUN, run_id)
            assert budget.consumed_usd <= budget.limit_usd, "并发下绝不能超支"
            assert budget.consumed_usd == Decimal("5.0")
    finally:
        await engine.dispose()


async def test_release_rolls_back_reservation(session_factory: object) -> None:
    """LLM 失败时 release 回滚预留。"""
    async with UnitOfWork(session_factory=session_factory) as uow:  # type: ignore[arg-type]
        run_id = await _run_id(uow)
    guard = BudgetGuard(session_factory=session_factory, settings=_settings())
    await guard.precheck(run_id=run_id, estimated_usd=Decimal("3.0"))
    rolled = await guard.release(run_id=run_id, reason="llm failed")
    assert rolled >= 1
    async with UnitOfWork(session_factory=session_factory) as uow:  # type: ignore[arg-type]
        budget = await uow.budgets.get_for_scope(BudgetScope.RUN, run_id)
        assert budget.consumed_usd == Decimal("0E-8")
