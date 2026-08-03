"""任务分发器 —— 候选拉取 → 依赖检查 → 路径冲突检查 → CAS 领取 → 投递。

来源：``docs/ARCHITECTURE.md`` §8.3.5、``docs/TASK_BREAKDOWN.md`` §6.1.1 / §11.6。

流程（:meth:`Dispatcher.dispatch_once`）
---------------------------------------
1. 拉取候选（``state=READY``，命中 ``ix_tasks_leasable``，读脏也无妨）；
2. 过滤：依赖已满足（``depends_on`` 全部终态）；
3. 路径冲突检查（US-09）：与当前 ``LEASED`` / ``ACTIVE`` Task 的
   ``touch_paths`` 重叠则跳过，并记 ``TASK_DEFERRED_PATH_CONFLICT``；
4. 调度策略排序（默认 ``dependency_aware``）；
5. 逐个 ``LeaseManager.acquire``（CAS），成功即返回租约；
6. 饥饿防护：连续被跳过超过 ``starvation_threshold`` 的任务提升有效优先级。

冻结契约（§11.6）::

    async def dispatch_once(self, *, worker_id: str, capabilities: Sequence[str]) -> Lease | None
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from loopforge.core.clock import get_clock
from loopforge.core.config import DispatcherSettings, get_settings
from loopforge.core.enums import ACTIVE_RUN_STATES, TERMINAL_TASK_STATES, TaskState
from loopforge.core.event_types import EventType
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork
from loopforge.models.lease import Lease
from loopforge.models.task import Task
from loopforge.orchestration.lease_manager import LeaseManager
from loopforge.orchestration.scheduling.strategies import (
    BaseSchedulingStrategy,
    default_strategy,
)

__all__ = ["Dispatcher"]


def has_path_conflict(candidate: Task, active_tasks: Sequence[Task]) -> bool:
    """路径冲突检测（§8.3.5 逐字逻辑）。

    :param candidate: 待派发任务。
    :param active_tasks: 当前正被执行的 Task 列表。
    :returns: 存在路径重叠返回 ``True``。
    """
    for active in active_tasks:
        if active.id == candidate.id:
            continue
        for p in candidate.touch_paths or []:
            for q in active.touch_paths or []:
                if p == q or p.startswith(q + "/") or q.startswith(p + "/"):
                    return True
    return False


class Dispatcher:
    """任务分发器（冻结契约 §11.6）。

    :param session_factory: session 工厂；``None`` 使用全局。
    :param settings: 分发配置；``None`` 使用全局。
    :param lease_manager: 租约管理器；``None`` 自动创建。
    :param strategy: 调度策略；``None`` 按配置创建。
    """

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        settings: DispatcherSettings | None = None,
        lease_manager: LeaseManager | None = None,
        strategy: BaseSchedulingStrategy | None = None,
    ) -> None:
        self._session_factory: Any = (
            session_factory if session_factory is not None else get_session_factory()
        )
        self._settings: DispatcherSettings = (
            settings if settings is not None else get_settings().dispatcher
        )
        self._lm: LeaseManager = lease_manager or LeaseManager(
            session_factory=self._session_factory
        )
        self._strategy: BaseSchedulingStrategy = strategy or default_strategy(
            self._settings.strategy
        )
        self._skip_counts: dict[str, int] = {}

    # -- 主入口 -------------------------------------------------------------

    async def dispatch_once(
        self, *, worker_id: str, capabilities: Sequence[str]
    ) -> Lease | None:
        """尝试为某个 Worker 领取一个可执行任务。

        :param worker_id: 领取者。
        :param capabilities: Worker 能力声明（暂用于软过滤与日志）。
        :returns: 领取成功的租约；无候选或全部被抢返回 ``None``。
        """
        now = get_clock().now()
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            # 1. 候选：READY 且所属 Run 处于活跃态。
            candidates = await uow.tasks.list_leasable(
                states=[TaskState.READY], limit=self._settings.batch_size
            )
            run_ids = {t.run_id for t in candidates}
            active_run_ids = set()
            for run_id in run_ids:
                run = await uow.runs.get(run_id)
                if run is not None and run.state in ACTIVE_RUN_STATES:
                    active_run_ids.add(run_id)
            candidates = [t for t in candidates if t.run_id in active_run_ids]

            # 2. 依赖过滤。
            ready: list[Task] = []
            for task in candidates:
                if await self._deps_satisfied(uow, task):
                    ready.append(task)
                else:
                    self._bump_skip(task.id)

            # 3. 路径冲突过滤（对照当前 LEASED/ACTIVE 任务）。
            active_tasks = await uow.tasks.list_leasable(
                states=[TaskState.LEASED, TaskState.ACTIVE],
                limit=200,
            )
            no_conflict: list[Task] = []
            for task in ready:
                if has_path_conflict(task, active_tasks):
                    uow.collect_event(
                        EventType.TASK_DEFERRED_PATH_CONFLICT,
                        {
                            "run_id": task.run_id,
                            "task_id": task.id,
                            "conflicting_task_id": (
                                active_tasks[0].id if active_tasks else ""
                            ),
                            "conflicting_paths": list(task.touch_paths or []),
                        },
                        run_id=task.run_id,
                        task_id=task.id,
                    )
                    self._bump_skip(task.id)
                else:
                    no_conflict.append(task)

            await uow.commit()
        if not no_conflict:
            return None

        # 4. 饥饿防护：被跳过多次的任务提升优先级。
        for task in no_conflict:
            effective_priority = self._effective_priority(task.id)
            task.priority = max(task.priority, effective_priority)
        ordered = self._strategy.order(no_conflict)

        # 5. 逐个 CAS 领取。
        for task in ordered:
            lease = await self._lm.acquire(
                task_id=task.id,
                worker_id=worker_id,
                expected_version=task.version,
                ttl_s=90,
            )
            if lease is not None:
                self._skip_counts.pop(task.id, None)
                return lease
            self._bump_skip(task.id)
        return None

    # -- 内部 ---------------------------------------------------------------

    async def _deps_satisfied(self, uow: UnitOfWork, task: Task) -> bool:
        """检查任务的全部依赖是否已进入终态。"""
        deps = task.depends_on or []
        if not deps:
            return True
        for dep_id in deps:
            dep = await uow.tasks.get(dep_id)
            if dep is None or dep.state not in TERMINAL_TASK_STATES:
                return False
        return True

    def _bump_skip(self, task_id: str) -> None:
        """记录一次跳过（饥饿防护计数）。"""
        self._skip_counts[task_id] = self._skip_counts.get(task_id, 0) + 1

    def _effective_priority(self, task_id: str) -> int:
        """被跳过超过阈值后，提升有效优先级。"""
        skips = self._skip_counts.get(task_id, 0)
        if skips < self._settings.starvation_threshold:
            return 0
        boost = (skips - self._settings.starvation_threshold + 1) * 10
        return 1000 + boost

    # -- 查询 ---------------------------------------------------------------

    def skip_count(self, task_id: str) -> int:
        """查询某 Task 当前的连续跳过次数（测试用）。"""
        return self._skip_counts.get(task_id, 0)
