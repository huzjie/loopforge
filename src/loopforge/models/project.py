"""项目与代码仓 ORM 模型。

对应表：``projects``、``repos``（``docs/ARCHITECTURE.md`` §5.1）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from loopforge.constants import ID_COLUMN_LENGTH
from loopforge.db.base import Base
from loopforge.db.mixins import IdMixin, TimestampMixin
from loopforge.db.types import JsonType

if TYPE_CHECKING:
    from loopforge.models.run import Run

__all__ = ["Project", "Repo"]


class Project(IdMixin, TimestampMixin, Base):
    """一个项目 = 一组代码仓 + 一组 Run 的归属容器。"""

    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("slug", name="uq_projects_slug"),)

    slug: Mapped[str] = mapped_column(String(64), nullable=False, doc="URL 友好唯一标识")
    name: Mapped[str] = mapped_column(String(200), nullable=False, doc="展示名")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, doc="项目描述")
    settings: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="项目级配置覆盖（配置第 6 层）"
    )

    repos: Mapped[list[Repo]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    runs: Mapped[list[Run]] = relationship(back_populates="project", lazy="noload")


class Repo(IdMixin, TimestampMixin, Base):
    """代码仓库配置（GitHub 远端或纯本地目录）。"""

    __tablename__ = "repos"
    __table_args__ = (
        Index("ix_repos_project_id", "project_id"),
        UniqueConstraint("project_id", "remote_url", name="uq_repos_project_remote"),
    )

    project_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default="local", doc="github / local"
    )
    remote_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    default_branch: Mapped[str] = mapped_column(String(200), nullable=False, default="main")
    credentials_ref: Mapped[str | None] = mapped_column(
        String(200), nullable=True, doc="指向 provider_configs / 环境变量名，不存明文"
    )
    local_path: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, doc="工作区绝对路径（POSIX 或 Windows 均可）"
    )
    labels_config: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="Issue 标签 → Task 字段映射"
    )
    webhook_secret_enc: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Fernet 密文，明文永不落库"
    )
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    project: Mapped[Project] = relationship(back_populates="repos", lazy="joined")
