"""Step / Checkpoint / IdempotencyRecord ORM 模型。

对应表：``steps``、``checkpoints``、``idempotency_records``
（``docs/ARCHITECTURE.md`` §5.1）。

**Step = 幂等执行单元**。写副作用之前先落一条 ``INTENT_RECORDED`` 的 Step
（含全局唯一 ``idempotency_key``），副作用成功后再置 ``CONFIRMED``。崩溃恢复时
扫描 ``ix_steps_unconfirmed`` 即可精确定位「意图已记录但结果未知」的操作，
再按 ``side_effect_class`` 决定重放 / 探测 / 转人工。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from loopforge.constants import ID_COLUMN_LENGTH
from loopforge.core.enums import RoleName, SideEffectClass, StepState
from loopforge.db.base import Base
from loopforge.db.mixins import CreatedAtMixin, IdMixin, TimestampMixin, utcnow
from loopforge.db.types import JsonType, StrEnumType, UtcDateTime

if TYPE_CHECKING:
    from loopforge.models.call import LlmCall, ToolCall

__all__ = ["Step", "Checkpoint", "IdempotencyRecord"]

#: 未确认 Step 的谓词，恢复扫描专用 partial index。
_UNCONFIRMED_PREDICATE: str = f"state = '{StepState.INTENT_RECORDED.value}'"


class Step(IdMixin, TimestampMixin, Base):
    """一个幂等执行单元。"""

    __tablename__ = "steps"
    __table_args__ = (
        CheckConstraint("loop_index >= 0", name="ck_steps_loop_index_non_negative"),
        CheckConstraint("attempt >= 0", name="ck_steps_attempt_non_negative"),
        CheckConstraint("fence_token >= 0", name="ck_steps_fence_token_non_negative"),
        UniqueConstraint("idempotency_key", name="uq_steps_idempotency_key"),
        Index("ix_steps_task_loop", "task_id", "loop_index"),
        Index(
            "ix_steps_unconfirmed",
            "state",
            "intent_at",
            sqlite_where=text(_UNCONFIRMED_PREDICATE),
            postgresql_where=text(_UNCONFIRMED_PREDICATE),
        ),
    )

    run_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=True,
    )
    loop_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="plan/code/review/test/summarize/vcs/…"
    )
    role: Mapped[RoleName | None] = mapped_column(StrEnumType(RoleName), nullable=True)
    state: Mapped[StepState] = mapped_column(
        StrEnumType(StepState), nullable=False, default=StepState.PENDING
    )

    #: 全局唯一幂等键，由 :func:`~loopforge.core.hashing.idempotency_key` 生成。
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)

    side_effect_class: Mapped[SideEffectClass] = mapped_column(
        StrEnumType(SideEffectClass), nullable=False, default=SideEffectClass.PURE
    )
    intent_payload: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="打算做什么（脱敏后）"
    )
    result_payload: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="实际结果（脱敏后）"
    )
    external_ref: Mapped[str | None] = mapped_column(
        String(500), nullable=True, doc="外部副作用句柄（PR 号 / commit sha / issue 号）"
    )
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fence_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    intent_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    llm_calls: Mapped[list[LlmCall]] = relationship(
        back_populates="step", cascade="all, delete-orphan", lazy="noload"
    )
    tool_calls: Mapped[list[ToolCall]] = relationship(
        back_populates="step", cascade="all, delete-orphan", lazy="noload"
    )

    # -- 领域方法 -----------------------------------------------------------

    @property
    def is_unconfirmed(self) -> bool:
        """意图已记录但结果未知 —— 崩溃恢复的重点关注对象。"""
        return self.state is StepState.INTENT_RECORDED

    @property
    def needs_manual_review(self) -> bool:
        """UNSAFE 副作用且未确认时，必须转人工而不是盲目重放。"""
        return self.is_unconfirmed and self.side_effect_class is SideEffectClass.UNSAFE


class Checkpoint(IdMixin, CreatedAtMixin, Base):
    """可回放的状态快照（step / loop / milestone 三种粒度）。"""

    __tablename__ = "checkpoints"
    __table_args__ = (
        CheckConstraint("loop_index >= 0", name="ck_checkpoints_loop_index_non_negative"),
        CheckConstraint("last_event_seq >= 0", name="ck_checkpoints_seq_non_negative"),
        Index("ix_checkpoints_run_loop", "run_id", "loop_index"),
        Index("ix_checkpoints_latest", "run_id", "created_at"),
    )

    run_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    loop_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="loop", doc="step / loop / milestone"
    )
    snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    last_step_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    last_event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", doc="snapshot 的 canonical sha256"
    )


class IdempotencyRecord(Base):
    """幂等记录表 —— API 层与工具层共用的「同一请求只执行一次」凭据。"""

    __tablename__ = "idempotency_records"
    __table_args__ = (Index("ix_idem_expires", "expires_at"),)

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default="api", doc="api / step / tool"
    )
    step_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    response_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="首次执行的响应，重放时原样返回"
    )
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", doc="请求体哈希，用于检测「同键不同体」"
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
