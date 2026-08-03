"""Worker ORM 模型 —— 执行器进程的注册与心跳。

对应表：``workers``（``docs/ARCHITECTURE.md`` §5.1）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from loopforge.constants import DEFAULT_HEARTBEAT_INTERVAL_S, ID_COLUMN_LENGTH
from loopforge.core.clock import get_clock
from loopforge.core.ids import new_ulid
from loopforge.db.base import Base
from loopforge.db.mixins import utcnow
from loopforge.db.types import JsonType, UtcDateTime

__all__ = ["Worker", "WORKER_STATES"]

#: Worker 状态取值（不进 enums.py —— 它不属于冻结的 11 组公共词汇）。
WORKER_STATES: tuple[str, ...] = ("idle", "busy", "draining", "dead")


class Worker(Base):
    """一个 Worker 进程实例。"""

    __tablename__ = "workers"
    __table_args__ = (
        CheckConstraint("pid >= 0", name="ck_workers_pid_non_negative"),
        Index("ix_workers_state_hb", "state", "last_heartbeat_at"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    hostname: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    pid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="支持的执行器 / 语言 / 工具集"
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    current_task_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH), nullable=True
    )
    drain: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, doc="优雅下线标记：不再领新任务"
    )
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )

    # -- 领域方法 -----------------------------------------------------------

    def is_stale(
        self,
        *,
        now: datetime | None = None,
        threshold_s: int = DEFAULT_HEARTBEAT_INTERVAL_S * 3,
    ) -> bool:
        """心跳超时判定 —— 连续错过 3 个心跳周期即视为失联。

        :param now: 参照时间；``None`` 表示取 :func:`~loopforge.core.clock.get_clock`。
        :param threshold_s: 超时阈值秒数。
        """
        moment = now if now is not None else get_clock().now()
        return moment - self.last_heartbeat_at > timedelta(seconds=threshold_s)

    @property
    def can_accept_task(self) -> bool:
        """空闲且未处于优雅下线时才可以领新任务。"""
        return self.state == "idle" and not self.drain
