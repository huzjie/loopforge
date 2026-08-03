"""看门狗 —— 过期租约 / 失联 Worker / 卡死 Run 的回收、重排、降级、上报。

来源：``docs/ARCHITECTURE.md`` §8.3、``docs/TASK_BREAKDOWN.md`` §6.1.1 / §11.6。

铁律 L4 落地
------------
Watchdog 回收一条租约前必须**双证据**：

1. 租约 ``state='ACTIVE'`` 且 ``expires_at < now - grace_period_s``；
2. 对应 Worker 的心跳也失联（``workers.last_heartbeat_at < now - grace_period_s``）
   或 Worker 行不存在。

慢 Worker（单步 5 分钟）只要还在心跳就**绝不误杀**。租约超时 ≠ 任务失败：
回收只加 ``reclaim_count``，绝不加 ``attempt_count``。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.clock import get_clock
from loopforge.core.config import LeaseSettings, get_settings
from loopforge.core.enums import RunState
from loopforge.core.event_types import EventType
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork
from loopforge.orchestration.lease_manager import LeaseManager

__all__ = ["Watchdog", "WatchdogReport"]


class WatchdogReport(BaseModel):
    """一次扫描的结果。"""

    model_config = ConfigDict(extra="forbid")

    scanned_leases: int = 0
    expired_leases: int = 0
    reclaimed: int = 0
    stuck_runs: int = 0
    duration_ms: int = 0


class Watchdog:
    """看门狗（冻结契约 §11.6）。

    :param session_factory: session 工厂；``None`` 使用全局。
    :param settings: 租约配置；``None`` 使用全局。
    :param lease_manager: 租约管理器；``None`` 自动创建。
    :param stuck_run_threshold_s: 判定 Run 卡死的时间阈值（无任何更新的秒数）。
    """

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        settings: LeaseSettings | None = None,
        lease_manager: LeaseManager | None = None,
        stuck_run_threshold_s: float = 600.0,
    ) -> None:
        self._session_factory: Any = (
            session_factory if session_factory is not None else get_session_factory()
        )
        self._settings: LeaseSettings = settings if settings is not None else get_settings().lease
        self._lm: LeaseManager = lease_manager or LeaseManager(
            session_factory=self._session_factory
        )
        self._stuck_threshold_s: float = stuck_run_threshold_s

    async def scan_once(self) -> WatchdogReport:
        """执行一轮扫描：回收孤儿租约 + 检测卡死 Run。

        :returns: 扫描报告。
        """
        started = get_clock().monotonic()
        grace_s = self._settings.grace_period_s
        reclaimed = await self._lm.reclaim_expired(grace_s=grace_s, limit=100)

        scanned = len(reclaimed)
        # 补充统计：宽限前已过期但未满足双证据的租约数量。
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            cutoff = get_clock().now() - timedelta(seconds=grace_s)
            expired = await uow.leases.list_expired(now=cutoff, limit=500)
            scanned = len(expired)
            uow.collect_event(
                EventType.WATCHDOG_SCAN,
                {
                    "scanned_leases": scanned,
                    "expired_leases": len(expired),
                    "reclaimed": len(reclaimed),
                    "stuck_runs": 0,
                    "duration_ms": int((get_clock().monotonic() - started) * 1000),
                },
            )
            await uow.commit()

        stuck = await self._detect_stuck_runs()

        return WatchdogReport(
            scanned_leases=scanned,
            expired_leases=len(reclaimed),
            reclaimed=len(reclaimed),
            stuck_runs=len(stuck),
            duration_ms=int((get_clock().monotonic() - started) * 1000),
        )

    async def _detect_stuck_runs(self) -> list[str]:
        """检测长时间无进展的 Run（LOOP_STUCK 上报，不自动回收）。"""
        now = get_clock().now()
        stuck_ids: list[str] = []
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            active_runs = await uow.runs.list_active(limit=200)
            for run in active_runs:
                if run.state not in (RunState.RUNNING,):
                    continue
                age_s = (now - run.updated_at).total_seconds()
                if age_s > self._stuck_threshold_s:
                    uow.collect_event(
                        EventType.LOOP_STUCK,
                        {
                            "run_id": run.id,
                            "task_id": None,
                            "loop_index": run.loop_index,
                            "stuck_seconds": int(age_s),
                            "last_event_seq": 0,
                        },
                        run_id=run.id,
                    )
                    stuck_ids.append(run.id)
            await uow.commit()
        return stuck_ids
