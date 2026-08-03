"""指标采集器 —— 从数据库聚合出运行时指标。

``/metrics`` 端点会调用这些采集器把 DB 实时状态刷进 Gauge/Counter，
让 Grafana 能看到「活」的数据，而不是只有进程启动以来的累加值。
"""

from __future__ import annotations

from sqlalchemy import func, select

from loopforge.core.logging import get_logger
from loopforge.db.repositories.governance_repo import ApprovalRepository
from loopforge.db.repositories.lease_repo import LeaseRepository
from loopforge.db.repositories.run_repo import RunRepository
from loopforge.db.repositories.system_repo import WorkerRepository
from loopforge.db.session import read_session
from loopforge.models.task import Task

from loopforge.observability.metrics import (
    APPROVALS_PENDING,
    LEASE_ACTIVE,
    RUNS_ACTIVE,
    RUNS_TOTAL,
    TASK_QUEUE_DEPTH,
    TASKS_TOTAL,
    WORKERS_TOTAL,
)

__all__ = ["collect_runtime_metrics"]

_logger = get_logger(__name__)

#: 计入「活跃 Run」的状态集合。
_ACTIVE_RUN_STATES = {
    "PLANNING",
    "READY",
    "RUNNING",
    "PAUSING",
    "PAUSED",
    "RECONCILING",
    "RECOVERING",
    "AWAITING_APPROVAL",
}

#: 计入「队列深度」的 Task 状态。
_QUEUE_TASK_STATES = {"CREATED", "READY"}


async def collect_runtime_metrics() -> None:
    """从数据库采集一轮实时指标（失败静默，不拖垮 /metrics）。"""
    try:
        async with read_session() as session:
            run_repo = RunRepository(session)
            counts = await run_repo.count_by_state()
            active = 0
            for state, count in counts.items():
                RUNS_TOTAL.labels(state=state).set(count)
                if state in _ACTIVE_RUN_STATES:
                    active += count
            RUNS_ACTIVE.set(active)

            result = await session.execute(
                select(Task.state, func.count()).group_by(Task.state)
            )
            task_counts = {str(state): int(count) for state, count in result.all()}
            queue = 0
            for state, count in task_counts.items():
                TASKS_TOTAL.labels(state=state).set(count)
                if state in _QUEUE_TASK_STATES:
                    queue += count
            TASK_QUEUE_DEPTH.set(queue)

            lease_repo = LeaseRepository(session)
            LEASE_ACTIVE.set(await lease_repo.count_active())

            worker_repo = WorkerRepository(session)
            worker_counts = await worker_repo.count_by_state()
            for state, count in worker_counts.items():
                WORKERS_TOTAL.labels(state=state).set(count)

            approval_repo = ApprovalRepository(session)
            APPROVALS_PENDING.set(await approval_repo.count_by(state="PENDING"))
    except Exception as exc:  # noqa: BLE001 —— 采集失败不影响主流程
        _logger.warning("collect_runtime_metrics_failed", error=str(exc))
