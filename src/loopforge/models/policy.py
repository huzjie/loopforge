"""Policy / PolicyRule / Approval ORM 模型 —— 策略引擎与人工审批。

对应表：``policies``、``policy_rules``、``approvals``
（``docs/ARCHITECTURE.md`` §5.1）。

策略以 YAML 编写、编译成结构化规则落库；``checksum`` 用于检测 YAML 是否被
篡改。同名策略在任意时刻只允许一条 ``active``（迁移 ``0006`` 用 partial
unique index 强制）。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
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
from loopforge.core.enums import PolicyDecision, Severity
from loopforge.core.ids import new_ulid
from loopforge.db.base import Base
from loopforge.db.mixins import utcnow
from loopforge.db.types import JsonType, StrEnumType, UtcDateTime

if TYPE_CHECKING:
    pass

__all__ = ["Policy", "PolicyRule", "Approval"]

_ACTIVE_POLICY_PREDICATE: str = "active = 1"
_ACTIVE_POLICY_PREDICATE_PG: str = "active"


class Policy(Base):
    """一份编译后的策略（name + version 唯一）。"""

    __tablename__ = "policies"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_policies_version_positive"),
        UniqueConstraint("name", "version", name="uq_policies_name_version"),
        Index(
            "ix_policies_active",
            "name",
            sqlite_where=text(_ACTIVE_POLICY_PREDICATE),
            postgresql_where=text(_ACTIVE_POLICY_PREDICATE_PG),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    yaml_source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    compiled: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="编译后的可执行规则树"
    )
    checksum: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", doc="yaml_source 的 sha256"
    )
    activated_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    rules: Mapped[list[PolicyRule]] = relationship(
        back_populates="policy",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PolicyRule.order_index",
    )


class PolicyRule(Base):
    """策略中的一条规则（按 ``order_index`` 从小到大求值，首个命中即裁决）。"""

    __tablename__ = "policy_rules"
    __table_args__ = (
        # 列名 order 是 SQL 保留字，原生 SQL 片段里必须显式加双引号。
        CheckConstraint('"order" >= 0', name="ck_policy_rules_order_non_negative"),
        UniqueConstraint("policy_id", "rule_id", name="uq_policy_rules_policy_rule"),
        Index("ix_rules_policy_order", "policy_id", "order"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    policy_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("policies.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_id: Mapped[str] = mapped_column(
        String(100), nullable=False, doc="策略内稳定标识，事件与审计里引用它"
    )
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, default="tool", doc="tool / path / network / cost / vcs"
    )
    match: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="匹配条件（glob / 正则 / 数值区间）"
    )
    effect: Mapped[PolicyDecision] = mapped_column(
        StrEnumType(PolicyDecision), nullable=False, default=PolicyDecision.ALLOW
    )
    #: 列名 ``order`` 是 SQL 保留字，属性名用 ``order_index`` 规避歧义。
    order_index: Mapped[int] = mapped_column("order", Integer, nullable=False, default=0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    severity: Mapped[Severity] = mapped_column(
        StrEnumType(Severity), nullable=False, default=Severity.INFO
    )

    policy: Mapped[Policy] = relationship(back_populates="rules", lazy="noload")


class Approval(Base):
    """人工审批单 —— 命中 ``REQUIRE_APPROVAL`` 时生成。"""

    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_state_expires", "state", "expires_at"),
        Index("ix_approvals_run", "run_id"),
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
    step_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    action_kind: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="待批准动作类型，如 tool.shell / vcs.merge"
    )
    action_payload: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="已脱敏的动作参数"
    )
    risk_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rule_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, doc="触发本审批的策略规则 rule_id"
    )
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        doc="pending / approved / rejected / expired",
    )
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_action: Mapped[str] = mapped_column(
        String(32), nullable=False, default="reject", doc="超时后的默认动作：reject / fail"
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    @property
    def is_pending(self) -> bool:
        """是否仍在等待人工决策。"""
        return self.state == "pending"
