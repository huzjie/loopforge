"""Task 仓储 —— 承载 **CAS 状态迁移** 这一并发正确性基石。

来源：``docs/ARCHITECTURE.md`` §7「零重复领取」、``docs/TASK_BREAKDOWN.md`` §3.1.3。

核心是 :meth:`TaskRepository.cas_transition`：::

    UPDATE tasks
       SET state = :to_state, version = version + 1, updated_at = :now, …
     WHERE id = :task_id
       AND state = :from_state
       AND version = :expected_version

``rowcount == 1`` 才算抢到；``0`` 表示别人先改了，调用方必须放弃并重试
下一个候选，**绝不能**读回来再写（那就退化成 read-modify-write 竞态了）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, asc, desc, func, select, update

from loopforge.core.clock import get_clock
from loopforge.core.enums import TERMINAL_TASK_STATES, TaskState
from loopforge.core.errors import IllegalTransitionError, OptimisticLockError
from loopforge.db.repositories.base import BaseRepository, clamp_limit
from loopforge.models.task import Task

__all__ = ["TaskRepository", "CasResult"]


class CasResult:
    """一次 CAS 尝试的结果。

    :ivar succeeded: 是否抢占成功（``rowcount == 1``）。
    :ivar new_version: 成功时的新版本号；失败时为 ``None``。
    :ivar rowcount: 底层 UPDATE 的受影响行数，便于排障。
    """

    __slots__ = ("succeeded", "new_version", "rowcount")

    def __init__(self, *, succeeded: bool, new_version: int | None, rowcount: int) -> None:
        self.succeeded = succeeded
        self.new_version = new_version
        self.rowcount = rowcount

    def __bool__(self) -> bool:
        """``if cas_result:`` 等价于判断是否成功。"""
        return self.succeeded

    def __repr__(self) -> str:
        return (
            f"CasResult(succeeded={self.succeeded}, "
            f"new_version={self.new_version}, rowcount={self.rowcount})"
        )


class TaskRepository(BaseRepository[Task]):
    """Task 持久化与并发原语。"""

    model = Task

    # -- CAS 原语 -----------------------------------------------------------

    async def cas_transition(
        self,
        task_id: str,
        *,
        from_state: TaskState,
        to_state: TaskState,
        expected_version: int,
        extra: dict[str, Any] | None = None,
        bump_fence: bool = False,
        now: datetime | None = None,
    ) -> CasResult:
        """条件 UPDATE 实现的原子状态迁移。

        :param task_id: 目标 Task 主键。
        :param from_state: 期望的当前状态；不匹配则 CAS 失败。
        :param expected_version: 期望的当前版本号；不匹配则 CAS 失败。
        :param to_state: 迁移后的目标状态。
        :param extra: 一并更新的其他字段（如 ``current_lease_id``、``blocked_reason``）。
        :param bump_fence: 是否同时把 ``fence_token`` +1（领取任务时必须为 ``True``）。
        :param now: 参照时间；``None`` 表示取全局 Clock。
        :returns: :class:`CasResult`；``succeeded=False`` 表示被别人抢先。
        :raises IllegalTransitionError: ``from_state`` 已是终态却要迁出。
        """
        if from_state in TERMINAL_TASK_STATES and to_state != from_state:
            raise IllegalTransitionError(
                "终态 Task 不允许再迁移",
                details={
                    "task_id": task_id,
                    "from": str(from_state),
                    "to": str(to_state),
                },
            )

        moment = now if now is not None else get_clock().now()
        new_version = expected_version + 1
        values: dict[str, Any] = {
            "state": to_state,
            "version": new_version,
            "updated_at": moment,
        }
        if bump_fence:
            values["fence_token"] = Task.fence_token + 1
        if extra:
            values.update(extra)

        stmt = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.state == from_state,
                Task.version == expected_version,
            )
            .values(**values)
        )
        result = await self.session.execute(stmt)
        rowcount = int(result.rowcount or 0)
        return CasResult(
            succeeded=rowcount == 1,
            new_version=new_version if rowcount == 1 else None,
            rowcount=rowcount,
        )

    async def cas_transition_or_raise(
        self,
        task_id: str,
        *,
        from_state: TaskState,
        to_state: TaskState,
        expected_version: int,
        extra: dict[str, Any] | None = None,
        bump_fence: bool = False,
        now: datetime | None = None,
    ) -> int:
        """:meth:`cas_transition` 的严格版：失败直接抛异常。

        :returns: 新版本号。
        :raises OptimisticLockError: CAS 失败（状态或版本已被他人改动）。
        """
        outcome = await self.cas_transition(
            task_id,
            from_state=from_state,
            to_state=to_state,
            expected_version=expected_version,
            extra=extra,
            bump_fence=bump_fence,
            now=now,
        )
        if not outcome.succeeded or outcome.new_version is None:
            raise OptimisticLockError(
                "Task CAS 迁移失败：状态或版本已被并发修改",
                details={
                    "task_id": task_id,
                    "from": str(from_state),
                    "to": str(to_state),
                    "expected_version": expected_version,
                },
            )
        return outcome.new_version

    async def bump_fence_token(self, task_id: str) -> int | None:
        """单独把 ``fence_token`` +1 并返回新值（租约续期不需要，重新签发才需要）。

        :returns: 新的 fence token；Task 不存在时为 ``None``。
        """
        stmt = (
            update(Task)
            .where(Task.id == task_id)
            .values(fence_token=Task.fence_token + 1, updated_at=get_clock().now())
        )
        result = await self.session.execute(stmt)
        if int(result.rowcount or 0) != 1:
            return None
        token = await self.session.execute(select(Task.fence_token).where(Task.id == task_id))
        return int(token.scalar_one())

    # -- Dispatcher 查询 ----------------------------------------------------

    def leasable_query(
        self,
        *,
        run_id: str | None = None,
        states: Sequence[TaskState] = (TaskState.READY,),
        limit: int | None = None,
    ) -> Select[tuple[Task]]:
        """构造「可领取任务候选」查询，命中 ``ix_tasks_leasable``。

        排序严格对齐索引键序：``state`` → ``priority DESC`` → ``created_at ASC``。

        :param run_id: 限定某个 Run；``None`` 表示跨 Run。
        :param states: 允许被领取的状态集合。
        :param limit: 候选条数。
        """
        stmt = select(Task).where(Task.state.in_(list(states)))
        if run_id is not None:
            stmt = stmt.where(Task.run_id == run_id)
        return (
            stmt.order_by(desc(Task.priority), asc(Task.created_at))
            .limit(clamp_limit(limit))
        )

    async def list_leasable(
        self,
        *,
        run_id: str | None = None,
        states: Sequence[TaskState] = (TaskState.READY,),
        limit: int | None = None,
    ) -> list[Task]:
        """取出可领取任务候选列表。"""
        result = await self.session.execute(
            self.leasable_query(run_id=run_id, states=states, limit=limit)
        )
        return list(result.scalars().all())

    async def list_by_run(
        self,
        run_id: str,
        *,
        states: Sequence[TaskState] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Task]:
        """列出某个 Run 下的 Task。"""
        stmt = select(Task).where(Task.run_id == run_id)
        if states:
            stmt = stmt.where(Task.state.in_(list(states)))
        stmt = (
            stmt.order_by(desc(Task.priority), asc(Task.created_at))
            .offset(max(0, offset))
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_key(self, run_id: str, task_key: str) -> Task | None:
        """按 ``(run_id, task_key)`` 取 Task（Run 内稳定标识）。"""
        result = await self.session.execute(
            select(Task).where(Task.run_id == run_id, Task.task_key == task_key)
        )
        return result.scalar_one_or_none()

    async def count_states(self, run_id: str) -> dict[str, int]:
        """统计某 Run 各状态的 Task 数量。

        :returns: ``{state_value: count}``，仅包含实际出现过的状态。
        """
        stmt = (
            select(Task.state, func.count())
            .where(Task.run_id == run_id)
            .group_by(Task.state)
        )
        result = await self.session.execute(stmt)
        return {str(state): int(count) for state, count in result.all()}

    async def all_terminal(self, run_id: str) -> bool:
        """某 Run 的全部 Task 是否都已进入终态（Run 收敛判定依据）。

        :returns: Run 下无 Task 时返回 ``False``（还没规划出任务，不算完成）。
        """
        total = await self.count_by(run_id=run_id)
        if total == 0:
            return False
        stmt = (
            select(func.count())
            .select_from(Task)
            .where(Task.run_id == run_id, Task.state.notin_(list(TERMINAL_TASK_STATES)))
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one()) == 0

    async def increment_attempt(self, task_id: str) -> int:
        """尝试计数 +1 并返回新值。

        :returns: 新的 ``attempt_count``；Task 不存在返回 ``-1``。
        """
        stmt = (
            update(Task)
            .where(Task.id == task_id)
            .values(attempt_count=Task.attempt_count + 1, updated_at=get_clock().now())
        )
        result = await self.session.execute(stmt)
        if int(result.rowcount or 0) != 1:
            return -1
        value = await self.session.execute(select(Task.attempt_count).where(Task.id == task_id))
        return int(value.scalar_one())

    async def increment_reclaim(self, task_id: str) -> int:
        """回收计数 +1 并返回新值。

        :returns: 新的 ``reclaim_count``；Task 不存在返回 ``-1``。
        """
        stmt = (
            update(Task)
            .where(Task.id == task_id)
            .values(reclaim_count=Task.reclaim_count + 1, updated_at=get_clock().now())
        )
        result = await self.session.execute(stmt)
        if int(result.rowcount or 0) != 1:
            return -1
        value = await self.session.execute(select(Task.reclaim_count).where(Task.id == task_id))
        return int(value.scalar_one())
