"""Run 仓储 —— 含 Run 级 CAS 状态迁移与循环推进。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from loopforge.core.clock import get_clock
from loopforge.core.enums import ACTIVE_RUN_STATES, TERMINAL_RUN_STATES, RunState
from loopforge.core.errors import IllegalTransitionError
from loopforge.db.repositories.base import BaseRepository, clamp_limit
from loopforge.models.run import Run

__all__ = ["RunRepository"]


class RunRepository(BaseRepository[Run]):
    """Run 持久化与状态机推进。"""

    model = Run

    # -- 状态迁移 -----------------------------------------------------------

    async def cas_state(
        self,
        run_id: str,
        *,
        from_state: RunState | Sequence[RunState],
        to_state: RunState,
        extra: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """条件 UPDATE 迁移 Run 状态。

        Run 没有 ``version`` 列（它不像 Task 那样被多 Worker 争抢），因此只比较
        ``state``；允许传入一组可接受的来源状态。

        :param from_state: 单个状态或状态序列。
        :param to_state: 目标状态。
        :param extra: 一并更新的字段。
        :param now: 参照时间。
        :returns: 是否迁移成功。
        :raises IllegalTransitionError: 源状态已是终态且目标不同。
        """
        sources = [from_state] if isinstance(from_state, RunState) else list(from_state)
        if not sources:
            raise IllegalTransitionError(
                "from_state 不能为空", details={"run_id": run_id, "to": str(to_state)}
            )
        for source in sources:
            if source in TERMINAL_RUN_STATES and source != to_state:
                raise IllegalTransitionError(
                    "终态 Run 不允许再迁移",
                    details={"run_id": run_id, "from": str(source), "to": str(to_state)},
                )

        moment = now if now is not None else get_clock().now()
        values: dict[str, Any] = {"state": to_state, "updated_at": moment}
        if to_state in TERMINAL_RUN_STATES:
            values["finished_at"] = moment
        if to_state is RunState.RUNNING:
            values["started_at"] = func.coalesce(Run.started_at, moment)
        if extra:
            values.update(extra)

        result = await self.session.execute(
            update(Run).where(Run.id == run_id, Run.state.in_(sources)).values(**values)
        )
        return int(result.rowcount or 0) == 1

    async def advance_loop(self, run_id: str, *, now: datetime | None = None) -> int:
        """``loop_index`` +1 并返回新值。

        :returns: 新的 ``loop_index``；Run 不存在返回 ``-1``。
        """
        moment = now if now is not None else get_clock().now()
        result = await self.session.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(loop_index=Run.loop_index + 1, updated_at=moment)
        )
        if int(result.rowcount or 0) != 1:
            return -1
        value = await self.session.execute(select(Run.loop_index).where(Run.id == run_id))
        return int(value.scalar_one())

    async def advance_milestone(self, run_id: str, *, now: datetime | None = None) -> int:
        """``milestone_index`` +1 并返回新值。

        :returns: 新的 ``milestone_index``；Run 不存在返回 ``-1``。
        """
        moment = now if now is not None else get_clock().now()
        result = await self.session.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(milestone_index=Run.milestone_index + 1, updated_at=moment)
        )
        if int(result.rowcount or 0) != 1:
            return -1
        value = await self.session.execute(select(Run.milestone_index).where(Run.id == run_id))
        return int(value.scalar_one())

    async def increment_recovery(self, run_id: str, *, now: datetime | None = None) -> int:
        """``recovery_count`` +1 并返回新值。

        :returns: 新的 ``recovery_count``；Run 不存在返回 ``-1``。
        """
        moment = now if now is not None else get_clock().now()
        result = await self.session.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(recovery_count=Run.recovery_count + 1, updated_at=moment)
        )
        if int(result.rowcount or 0) != 1:
            return -1
        value = await self.session.execute(select(Run.recovery_count).where(Run.id == run_id))
        return int(value.scalar_one())

    # -- 查询 ---------------------------------------------------------------

    async def list_active(self, *, limit: int | None = None) -> list[Run]:
        """列出全部活跃 Run（命中 ``ix_runs_active``）。"""
        stmt = (
            select(Run)
            .where(Run.state.in_(list(ACTIVE_RUN_STATES)))
            .order_by(Run.updated_at)
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_project(
        self,
        project_id: str,
        *,
        states: Sequence[RunState] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Run]:
        """列出某项目下的 Run，按创建时间倒序。"""
        stmt = select(Run).where(Run.project_id == project_id)
        if states:
            stmt = stmt.where(Run.state.in_(list(states)))
        stmt = (
            stmt.order_by(Run.created_at.desc())
            .offset(max(0, offset))
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_state(self) -> dict[str, int]:
        """全局各状态 Run 数量（指标导出用）。"""
        result = await self.session.execute(
            select(Run.state, func.count()).group_by(Run.state)
        )
        return {str(state): int(count) for state, count in result.all()}

    async def is_exhausted(self, run_id: str) -> bool:
        """循环额度是否已耗尽（``loop_index >= max_loops``）。

        :returns: Run 不存在时返回 ``False``。
        """
        result = await self.session.execute(
            select(Run.loop_index, Run.max_loops).where(Run.id == run_id)
        )
        row = result.one_or_none()
        if row is None:
            return False
        return int(row[0]) >= int(row[1])
