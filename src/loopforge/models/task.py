"""Task（可被 Worker 领取的最小工作单元）ORM 模型。

对应表：``tasks``（``docs/ARCHITECTURE.md`` §5.1）。

.. important::
   ``version`` 字段是**应用层 CAS** 的比较位，**不是** SQLAlchemy 的
   ``version_id_col``。领取任务走
   :meth:`~loopforge.db.repositories.task_repo.TaskRepository.cas_transition`
   的条件 UPDATE，而不是 ORM 的隐式乐观锁 —— 因为我们需要在一条 SQL 里同时
   比较 state 与 version，并拿到 ``rowcount`` 判定成败。
"""

from __future__ import annotations

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

from loopforge.constants import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_RECLAIMS,
    ID_COLUMN_LENGTH,
)
from loopforge.core.enums import TERMINAL_TASK_STATES, TaskState
from loopforge.db.base import Base
from loopforge.db.mixins import IdMixin, TimestampMixin
from loopforge.db.types import JsonType, StrEnumType

if TYPE_CHECKING:
    from loopforge.models.lease import Lease
    from loopforge.models.run import Run

__all__ = ["Task"]


class Task(IdMixin, TimestampMixin, Base):
    """一个 Task：标题 + 正文 + 依赖 + 状态 + 租约信息。"""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("version >= 0", name="ck_tasks_version_non_negative"),
        CheckConstraint("priority >= 0", name="ck_tasks_priority_non_negative"),
        CheckConstraint("attempt_count >= 0", name="ck_tasks_attempt_count_non_negative"),
        CheckConstraint("reclaim_count >= 0", name="ck_tasks_reclaim_count_non_negative"),
        CheckConstraint("fence_token >= 0", name="ck_tasks_fence_token_non_negative"),
        UniqueConstraint("run_id", "task_key", name="uq_tasks_run_key"),
        UniqueConstraint("repo_id", "external_issue_number", name="uq_tasks_repo_issue"),
        # Dispatcher 唯一入口：state 过滤 + priority 倒序 + 创建时间正序。
        # 用 text("priority DESC") 而非字符串列名，才能真正生成降序索引键；
        # SQLite 与 PostgreSQL 都支持索引列上的 DESC 修饰。
        Index("ix_tasks_leasable", "state", text("priority DESC"), "created_at"),
        Index("ix_tasks_run_state", "run_id", "state"),
    )

    run_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("repos.id", ondelete="SET NULL"),
        nullable=True,
    )
    task_key: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="Run 内稳定短标识，如 T-0007"
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state: Mapped[TaskState] = mapped_column(
        StrEnumType(TaskState), nullable=False, default=TaskState.CREATED
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, doc="数值越大越优先"
    )
    depends_on: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, default=list, doc="前置 task_id 列表（DAG 边）"
    )
    touch_paths: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, default=list, doc="预计改动的路径，用于冲突检测"
    )

    #: **CAS 比较位**：每次成功状态迁移 +1。
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reclaim_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_MAX_ATTEMPTS
    )
    max_reclaims: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_MAX_RECLAIMS
    )
    current_lease_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH), nullable=True
    )

    #: **Fencing token**：单调递增，旧租约持有者的写操作凭此被拒绝。
    fence_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    external_issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="终态产出摘要"
    )

    run: Mapped[Run] = relationship(back_populates="tasks", lazy="noload")
    leases: Mapped[list[Lease]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="noload"
    )

    # -- 领域方法 -----------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        """是否已进入终态（DONE / FAILED / CANCELLED）。"""
        return self.state in TERMINAL_TASK_STATES

    @property
    def attempts_remaining(self) -> int:
        """剩余尝试次数（永不为负）。"""
        return max(0, self.max_attempts - self.attempt_count)

    @property
    def reclaims_remaining(self) -> int:
        """剩余可被回收次数（永不为负）。"""
        return max(0, self.max_reclaims - self.reclaim_count)

    def can_retry(self) -> bool:
        """尝试次数与回收次数都还有余量时才允许重试。"""
        return self.attempts_remaining > 0 and self.reclaims_remaining > 0
