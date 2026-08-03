"""监控器 —— Run / Task / Worker / Step 实时状态、进度、成本速率快照与流。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1 / §11.6。

冻结契约（§11.6）::

    class Monitor:
        async def snapshot(self, *, run_id: str | None = None) -> MonitorSnapshot: ...
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.clock import get_clock
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork

__all__ = ["Monitor", "MonitorSnapshot", "RunMonitor", "TaskMonitor", "WorkerMonitor"]


class RunMonitor(BaseModel):
    """Run 实时快照。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    project_id: str = ""
    state: str
    loop_index: int = 0
    max_loops: int = 0
    tasks_total: int = 0
    tasks_done: int = 0
    tasks_failed: int = 0
    recovery_count: int = 0
    updated_at: Any = None


class TaskMonitor(BaseModel):
    """Task 实时快照。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    run_id: str = ""
    task_key: str = ""
    title: str = ""
    state: str
    priority: int = 0
    attempt_count: int = 0
    reclaim_count: int = 0
    version: int = 0
    current_lease_id: str | None = None
    fence_token: int = 0


class WorkerMonitor(BaseModel):
    """Worker 实时快照。"""

    model_config = ConfigDict(extra="forbid")

    worker_id: str
    state: str = "idle"
    current_task_id: str | None = None
    drain: bool = False
    last_heartbeat_at: Any = None
    pid: int = 0


class StepMonitor(BaseModel):
    """Step 实时快照。"""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    run_id: str = ""
    task_id: str | None = None
    phase: str = ""
    state: str
    loop_index: int = 0
    idempotency_key: str = ""
    side_effect_class: str = "PURE"
    fence_token: int = 0


class MonitorSnapshot(BaseModel):
    """整体快照（冻结契约 §11.6 的 ``MonitorSnapshot``）。"""

    model_config = ConfigDict(extra="forbid")

    runs: list[RunMonitor] = Field(default_factory=list)
    tasks: list[TaskMonitor] = Field(default_factory=list)
    workers: list[WorkerMonitor] = Field(default_factory=list)
    steps: list[StepMonitor] = Field(default_factory=list)
    active_leases: int = 0
    captured_at: Any = None


class Monitor:
    """只读监控器。

    :param session_factory: session 工厂；``None`` 使用全局。
    """

    def __init__(self, *, session_factory: Any | None = None) -> None:
        self._session_factory: Any = (
            session_factory if session_factory is not None else get_session_factory()
        )

    async def snapshot(self, *, run_id: str | None = None) -> MonitorSnapshot:
        """抓取当前快照。

        :param run_id: 限定某个 Run；``None`` 返回全局。
        """
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            runs = (
                [await uow.runs.get_or_raise(run_id)]
                if run_id
                else await uow.runs.list_active(limit=200)
            )
            run_monitors = [
                RunMonitor(
                    run_id=r.id,
                    project_id=r.project_id,
                    state=r.state.value,
                    loop_index=r.loop_index,
                    max_loops=r.max_loops,
                    recovery_count=r.recovery_count,
                    updated_at=r.updated_at,
                )
                for r in runs
            ]
            tasks: list[Any] = []
            if run_id:
                tasks = await uow.tasks.list_by_run(run_id, limit=500)
            else:
                # 全局：取每个活跃 Run 的任务，控制上限。
                for r in runs[:10]:
                    tasks.extend(await uow.tasks.list_by_run(r.id, limit=50))
            task_monitors = [
                TaskMonitor(
                    task_id=t.id,
                    run_id=t.run_id,
                    task_key=t.task_key,
                    title=t.title,
                    state=t.state.value,
                    priority=t.priority,
                    attempt_count=t.attempt_count,
                    reclaim_count=t.reclaim_count,
                    version=t.version,
                    current_lease_id=t.current_lease_id,
                    fence_token=t.fence_token,
                )
                for t in tasks
            ]
            workers = await uow.system.workers.list_alive(limit=200)
            worker_monitors = [
                WorkerMonitor(
                    worker_id=w.id,
                    state=w.state,
                    current_task_id=w.current_task_id,
                    drain=w.drain,
                    last_heartbeat_at=w.last_heartbeat_at,
                    pid=w.pid,
                )
                for w in workers
            ]
            step_rows: list[Any] = []
            if run_id:
                step_rows = await uow.steps.list_unconfirmed(run_id=run_id, limit=200)
            step_monitors = [
                StepMonitor(
                    step_id=s.id,
                    run_id=s.run_id,
                    task_id=s.task_id,
                    phase=s.phase,
                    state=s.state.value,
                    loop_index=s.loop_index,
                    idempotency_key=s.idempotency_key,
                    side_effect_class=s.side_effect_class.value,
                    fence_token=s.fence_token,
                )
                for s in step_rows
            ]
            active_leases = await uow.leases.count_active()
            return MonitorSnapshot(
                runs=run_monitors,
                tasks=task_monitors,
                workers=worker_monitors,
                steps=step_monitors,
                active_leases=active_leases,
                captured_at=get_clock().now(),
            )

    async def stream(
        self, *, run_id: str | None = None, interval_s: float = 2.0
    ) -> AsyncIterator[MonitorSnapshot]:
        """轮询式快照流（供 WS / CLI 消费）。

        :param run_id: 限定 Run。
        :param interval_s: 轮询间隔。
        """
        while True:
            yield await self.snapshot(run_id=run_id)
            await asyncio.sleep(interval_s)
