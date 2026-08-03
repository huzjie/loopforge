"""LlmCall / ToolCall ORM 模型 —— 单次调用级的成本与行为遥测。

对应表：``llm_calls``、``tool_calls``（``docs/ARCHITECTURE.md`` §5.1）。

``request`` / ``response`` / ``args`` / ``result`` 落库前**必须**经过
:func:`loopforge.core.redaction.redact_value` 脱敏；仓储层
:class:`~loopforge.db.repositories.step_repo.LlmCallRepository` 会强制执行。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from loopforge.constants import ID_COLUMN_LENGTH
from loopforge.core.enums import PolicyDecision, RoleName
from loopforge.core.ids import new_ulid
from loopforge.db.base import Base
from loopforge.db.mixins import utcnow
from loopforge.db.types import MONEY_ZERO, DecimalMoney, JsonType, StrEnumType, UtcDateTime

if TYPE_CHECKING:
    from loopforge.models.step import Step

__all__ = ["LlmCall", "ToolCall"]


class LlmCall(Base):
    """一次 LLM 请求的完整记录（含 token 与花费）。"""

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint("prompt_tokens >= 0", name="ck_llm_calls_prompt_tokens_non_negative"),
        CheckConstraint(
            "completion_tokens >= 0", name="ck_llm_calls_completion_tokens_non_negative"
        ),
        CheckConstraint("cached_tokens >= 0", name="ck_llm_calls_cached_tokens_non_negative"),
        CheckConstraint("cost_usd >= 0", name="ck_llm_calls_cost_non_negative"),
        CheckConstraint("latency_ms >= 0", name="ck_llm_calls_latency_non_negative"),
        Index("ix_llm_calls_step", "step_id"),
        Index("ix_llm_calls_model_created", "model", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    step_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("steps.id", ondelete="CASCADE"),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[RoleName | None] = mapped_column(StrEnumType(RoleName), nullable=True)
    request: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="已脱敏的请求体"
    )
    response: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="已脱敏的响应体"
    )
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(
        DecimalMoney, nullable=False, default=lambda: MONEY_ZERO
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failover_from: Mapped[str | None] = mapped_column(
        String(64), nullable=True, doc="若为故障转移调用，记录原 provider 名"
    )
    finish_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    step: Mapped[Step | None] = relationship(back_populates="llm_calls", lazy="noload")

    @property
    def total_tokens(self) -> int:
        """prompt + completion（不含 cached，避免重复计数）。"""
        return self.prompt_tokens + self.completion_tokens


class ToolCall(Base):
    """一次工具调用的记录（含策略裁决与输出截断信息）。"""

    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint("duration_ms >= 0", name="ck_tool_calls_duration_non_negative"),
        CheckConstraint("bytes_out >= 0", name="ck_tool_calls_bytes_out_non_negative"),
        Index("ix_tool_calls_step", "step_id"),
        Index("ix_tool_calls_name", "tool_name"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    step_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("steps.id", ondelete="CASCADE"),
        nullable=True,
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="已脱敏的入参"
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="已脱敏且已截断的出参"
    )
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    policy_decision: Mapped[PolicyDecision] = mapped_column(
        StrEnumType(PolicyDecision), nullable=False, default=PolicyDecision.ALLOW
    )
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bytes_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stderr_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    step: Mapped[Step | None] = relationship(back_populates="tool_calls", lazy="noload")

    @property
    def succeeded(self) -> bool:
        """退出码为 0 视为成功；``None``（未执行/被拒）不算成功。"""
        return self.exit_code == 0
