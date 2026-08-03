"""Run（一次长程自主编程任务）ORM 模型。

对应表：``runs``（``docs/ARCHITECTURE.md`` §5.1）。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from loopforge.constants import DEFAULT_MAX_LOOPS, ID_COLUMN_LENGTH
from loopforge.core.enums import ACTIVE_RUN_STATES, RunState
from loopforge.db.base import Base
from loopforge.db.mixins import IdMixin, TimestampMixin
from loopforge.db.types import JsonType, StrEnumType, UtcDateTime

if TYPE_CHECKING:
    from loopforge.models.project import Project, Repo
    from loopforge.models.task import Task

__all__ = ["Run"]

#: 「活跃 Run」谓词，同时用于 SQLite / PostgreSQL 的 partial index。
#: 直接由 :data:`~loopforge.core.enums.ACTIVE_RUN_STATES` 生成，避免手写漂移。
_ACTIVE_STATE_PREDICATE: str = "state IN (" + ", ".join(
    f"'{member.value}'" for member in sorted(ACTIVE_RUN_STATES, key=lambda m: m.value)
) + ")"


class Run(IdMixin, TimestampMixin, Base):
    """一次 Run：目标 + 约束 + 状态机 + 循环计数。"""

    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("loop_index >= 0", name="ck_runs_loop_index_non_negative"),
        CheckConstraint("milestone_index >= 0", name="ck_runs_milestone_index_non_negative"),
        CheckConstraint("max_loops > 0", name="ck_runs_max_loops_positive"),
        CheckConstraint("recovery_count >= 0", name="ck_runs_recovery_count_non_negative"),
        Index("ix_runs_state", "state"),
        Index("ix_runs_project_created", "project_id", "created_at"),
        Index(
            "ix_runs_active",
            "state",
            "updated_at",
            sqlite_where=text(_ACTIVE_STATE_PREDICATE),
            postgresql_where=text(_ACTIVE_STATE_PREDICATE),
        ),
    )

    project_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("repos.id", ondelete="SET NULL"),
        nullable=True,
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False, doc="自然语言总目标")
    constraints: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="硬约束（禁改路径、必须通过的测试等）"
    )
    state: Mapped[RunState] = mapped_column(
        StrEnumType(RunState), nullable=False, default=RunState.CREATED
    )
    loop_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    milestone_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_loops: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_MAX_LOOPS)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="Run 启动瞬间的有效配置快照（可复现）"
    )
    budget_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    policy_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    recovery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    project: Mapped[Project] = relationship(back_populates="runs", lazy="noload")
    repo: Mapped[Repo | None] = relationship(lazy="noload")
    tasks: Mapped[list[Task]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="noload"
    )

    # -- 领域方法 -----------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """当前状态是否属于「活跃」集合（Watchdog / Dispatcher 只看活跃 Run）。"""
        return self.state in ACTIVE_RUN_STATES

    @property
    def loops_remaining(self) -> int:
        """剩余可用循环数（永不为负）。"""
        return max(0, self.max_loops - self.loop_index)
