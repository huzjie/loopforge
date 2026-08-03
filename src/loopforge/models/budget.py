"""Budget / CostRecord ORM 模型 —— 成本治理的账本。

对应表：``budgets``、``cost_records``（``docs/ARCHITECTURE.md`` §5.1）。

阈值语义：``warn_at <= throttle_at <= halt_at <= 1.0``，均为占 ``limit_usd``
的比例。越过 ``halt_at`` 时 Run 被强制转入 ``HALTED``。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from loopforge.constants import ID_COLUMN_LENGTH
from loopforge.core.enums import BudgetAction, BudgetScope
from loopforge.core.ids import new_ulid
from loopforge.db.base import Base
from loopforge.db.mixins import utcnow
from loopforge.db.types import MONEY_ZERO, DecimalMoney, StrEnumType, UtcDateTime

if TYPE_CHECKING:
    pass

__all__ = ["Budget", "CostRecord"]

_ACTIVE_BUDGET_PREDICATE: str = "active = 1"
_ACTIVE_BUDGET_PREDICATE_PG: str = "active"


class Budget(Base):
    """一条预算：作用域 + 上限 + 三级阈值 + 已消耗。"""

    __tablename__ = "budgets"
    __table_args__ = (
        CheckConstraint("limit_usd >= 0", name="ck_budgets_limit_non_negative"),
        CheckConstraint("consumed_usd >= 0", name="ck_budgets_consumed_non_negative"),
        CheckConstraint(
            "warn_at <= throttle_at AND throttle_at <= halt_at AND halt_at <= 1.0",
            name="ck_budgets_threshold_order",
        ),
        CheckConstraint("warn_at >= 0.0", name="ck_budgets_warn_non_negative"),
        UniqueConstraint("scope", "scope_ref", "window", name="uq_budgets_scope_ref_window"),
        Index(
            "ix_budgets_active",
            "scope",
            "window_start",
            sqlite_where=text(_ACTIVE_BUDGET_PREDICATE),
            postgresql_where=text(_ACTIVE_BUDGET_PREDICATE_PG),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    scope: Mapped[BudgetScope] = mapped_column(
        StrEnumType(BudgetScope), nullable=False, default=BudgetScope.RUN
    )
    scope_ref: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        nullable=False,
        default="",
        doc="RUN → run_id；DAY → YYYY-MM-DD；ORG → 组织标识",
    )
    limit_usd: Mapped[Decimal] = mapped_column(
        DecimalMoney, nullable=False, default=lambda: MONEY_ZERO
    )
    window: Mapped[str] = mapped_column(
        String(32), nullable=False, default="total", doc="total / daily / monthly"
    )
    warn_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    throttle_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    halt_at: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    consumed_usd: Mapped[Decimal] = mapped_column(
        DecimalMoney, nullable=False, default=lambda: MONEY_ZERO
    )
    window_start: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    # -- 领域方法 -----------------------------------------------------------

    @property
    def usage_ratio(self) -> float:
        """已消耗 / 上限。上限为 0 时返回 ``0.0``（视作不限额）。"""
        if self.limit_usd <= 0:
            return 0.0
        return float(self.consumed_usd / self.limit_usd)

    @property
    def remaining_usd(self) -> Decimal:
        """剩余额度（永不为负）。"""
        remainder = self.limit_usd - self.consumed_usd
        return remainder if remainder > 0 else Decimal("0E-8")

    def triggered_action(self) -> BudgetAction | None:
        """按当前使用率返回应触发的动作。

        :returns: :class:`~loopforge.core.enums.BudgetAction`；未越过任何阈值时为 ``None``。
        """
        ratio = self.usage_ratio
        if ratio >= self.halt_at:
            return BudgetAction.HALT
        if ratio >= self.throttle_at:
            return BudgetAction.THROTTLE
        if ratio >= self.warn_at:
            return BudgetAction.WARN
        return None


class CostRecord(Base):
    """一条花费流水 —— 五维（run/task/pr/provider/model）归因下钻的基础。"""

    __tablename__ = "cost_records"
    __table_args__ = (
        CheckConstraint("cost_usd >= 0", name="ck_cost_records_cost_non_negative"),
        CheckConstraint("prompt_tokens >= 0", name="ck_cost_records_prompt_non_negative"),
        CheckConstraint(
            "completion_tokens >= 0", name="ck_cost_records_completion_non_negative"
        ),
        Index("ix_cost_run_time", "run_id", "occurred_at"),
        Index("ix_cost_model_time", "model", "occurred_at"),
        Index("ix_cost_task", "task_id"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    run_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    pr_ref: Mapped[str | None] = mapped_column(
        String(200), nullable=True, doc="PR 号或 URL，用于按 PR 归因"
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(
        DecimalMoney, nullable=False, default=lambda: MONEY_ZERO
    )
    llm_call_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    budget_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )
