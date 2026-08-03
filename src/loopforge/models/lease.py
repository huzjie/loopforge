"""Lease（任务租约）ORM 模型。

对应表：``leases``（``docs/ARCHITECTURE.md`` §5.1）。

**双保险设计**：

1. **应用层 CAS** —— :meth:`~loopforge.db.repositories.lease_repo.LeaseRepository.acquire_lease_cas`
   用条件 UPDATE 抢占任务；
2. **数据库层 partial unique index** —— ``uq_leases_active_task`` 保证同一 task
   最多只有 1 条 ``state='ACTIVE'`` 的租约。即便应用层出现逻辑漏洞，DB 也会抛
   ``IntegrityError``，Worker 静默跳过去抢下一个任务。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from loopforge.constants import DEFAULT_LEASE_TTL_S, ID_COLUMN_LENGTH, MAX_LEASE_TTL_S
from loopforge.core.clock import get_clock
from loopforge.core.enums import LeaseState
from loopforge.db.base import Base
from loopforge.db.mixins import IdMixin
from loopforge.db.types import StrEnumType, UtcDateTime

if TYPE_CHECKING:
    from loopforge.models.task import Task

__all__ = ["Lease"]

#: partial unique index 的谓词，SQLite / PostgreSQL 语法一致。
_ACTIVE_LEASE_PREDICATE: str = f"state = '{LeaseState.ACTIVE.value}'"


class Lease(IdMixin, Base):
    """一条租约：worker 在 TTL 内对 task 的独占执行权。"""

    __tablename__ = "leases"
    __table_args__ = (
        CheckConstraint("ttl_seconds > 0", name="ck_leases_ttl_positive"),
        CheckConstraint("max_ttl_seconds >= ttl_seconds", name="ck_leases_max_ttl_ge_ttl"),
        CheckConstraint("fence_token >= 0", name="ck_leases_fence_token_non_negative"),
        CheckConstraint("heartbeat_count >= 0", name="ck_leases_heartbeat_count_non_negative"),
        CheckConstraint("extend_count >= 0", name="ck_leases_extend_count_non_negative"),
        Index("ix_leases_expiry", "state", "expires_at"),
        Index("ix_leases_task", "task_id"),
        Index("ix_leases_worker", "worker_id"),
        Index(
            "uq_leases_active_task",
            "task_id",
            unique=True,
            sqlite_where=text(_ACTIVE_LEASE_PREDICATE),
            postgresql_where=text(_ACTIVE_LEASE_PREDICATE),
        ),
    )

    task_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    worker_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), nullable=False)

    #: 与 ``tasks.fence_token`` 同源，租约签发时快照。
    fence_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    state: Mapped[LeaseState] = mapped_column(
        StrEnumType(LeaseState), nullable=False, default=LeaseState.ACTIVE
    )
    ttl_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_LEASE_TTL_S
    )
    max_ttl_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=MAX_LEASE_TTL_S
    )
    acquired_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    heartbeat_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extend_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expiry_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    task: Mapped[Task] = relationship(back_populates="leases", lazy="noload")

    # -- 领域方法 -----------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """状态为 ACTIVE 即视为活跃（是否过期由 :meth:`is_expired` 单独判定）。"""
        return self.state is LeaseState.ACTIVE

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """判断租约是否已过期。

        :param now: 参照时间；``None`` 表示取 :func:`~loopforge.core.clock.get_clock`。
        """
        moment = now if now is not None else get_clock().now()
        return moment >= self.expires_at

    def seconds_to_expiry(self, *, now: datetime | None = None) -> float:
        """距离过期还有多少秒（已过期则为负）。"""
        moment = now if now is not None else get_clock().now()
        return (self.expires_at - moment).total_seconds()

    def next_expiry(self, *, now: datetime | None = None) -> datetime:
        """按 ``ttl_seconds`` 计算下一个过期时刻，并夹在 ``max_ttl_seconds`` 内。

        :returns: 新的过期时间，且不超过 ``acquired_at + max_ttl_seconds``。
        """
        moment = now if now is not None else get_clock().now()
        candidate = moment + timedelta(seconds=self.ttl_seconds)
        hard_limit = self.acquired_at + timedelta(seconds=self.max_ttl_seconds)
        return candidate if candidate <= hard_limit else hard_limit
