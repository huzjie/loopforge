"""Event / AuditLog ORM 模型（**append-only**）。

对应表：``events``、``audit_logs``（``docs/ARCHITECTURE.md`` §5.1）。

**append-only 双保险**：

1. **应用层**：仓储只暴露 ``append()``，不提供 update / delete；
2. **数据库层**：迁移 ``0006`` 为两张表建 ``BEFORE UPDATE`` / ``BEFORE DELETE``
   触发器，直接 ``RAISE(ABORT, …)``。

``events.seq`` 是**自增整数主键**（PG 用 BIGSERIAL，SQLite 用
``INTEGER PRIMARY KEY AUTOINCREMENT``），保证同一 Run 的事件全序可回放；
``id`` 仍保留 ULID 供外部引用。``prev_hash`` / ``hash`` 构成哈希链，
任何篡改都会在校验时暴露。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from loopforge.constants import ID_COLUMN_LENGTH
from loopforge.core.enums import Severity
from loopforge.core.hashing import chain_hash
from loopforge.core.ids import new_ulid
from loopforge.db.base import Base
from loopforge.db.mixins import utcnow
from loopforge.db.types import JsonType, StrEnumType, UtcDateTime

__all__ = ["Event", "AuditLog"]

#: SQLite 不认识 BIGSERIAL，用 ``with_variant`` 退化成普通 INTEGER 自增主键。
_SeqType = BigInteger().with_variant(Integer(), "sqlite")


class Event(Base):
    """事件流的一条记录。**只增不改不删。**"""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("hash", name="uq_events_hash"),
        UniqueConstraint("id", name="uq_events_id"),
        Index("ix_events_run_seq", "run_id", "seq"),
        Index("ix_events_type", "type"),
        Index("ix_events_trace", "trace_id"),
        Index("ix_events_created", "created_at"),
    )

    #: 自增全序序号 —— 事件回放的排序依据。
    seq: Mapped[int] = mapped_column(_SeqType, primary_key=True, autoincrement=True)

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), nullable=False, default=new_ulid
    )
    run_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    step_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    type: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="EventType 的字符串值（如 run.started）"
    )
    severity: Mapped[Severity] = mapped_column(
        StrEnumType(Severity), nullable=False, default=Severity.INFO
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, doc="上一条事件的 hash；首条为 None"
    )
    hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", doc="chain_hash(prev_hash, payload)"
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    # -- 领域方法 -----------------------------------------------------------

    def compute_hash(self) -> str:
        """按当前 ``prev_hash`` + ``payload`` 重新计算链式哈希。

        :returns: 64 位小写 hex。
        """
        return chain_hash(self.prev_hash, self.chain_body())

    def chain_body(self) -> dict[str, Any]:
        """参与哈希计算的规范化字段集合（顺序无关，由 canonical_json 保证）。"""
        return {
            "id": self.id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "type": self.type,
            "severity": str(self.severity),
            "payload": self.payload,
        }

    def verify(self) -> bool:
        """校验自身 ``hash`` 是否与内容一致（防篡改自检）。"""
        return bool(self.hash) and self.hash == self.compute_hash()


class AuditLog(Base):
    """人 / API Key / 系统 / 智能体的敏感动作审计。**只增不改不删。**"""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_resource", "resource_type", "resource_id"),
        Index("ix_audit_actor_created", "actor", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    actor: Mapped[str] = mapped_column(String(200), nullable=False, default="system")
    actor_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="system", doc="user / apikey / system / agent"
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    before: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
