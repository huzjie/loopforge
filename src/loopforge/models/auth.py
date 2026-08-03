"""User / ApiKey ORM 模型 —— 认证与授权主体。

对应表：``users``、``api_keys``（``docs/ARCHITECTURE.md`` §5.1）。

.. warning::
   ``hashed_password`` / ``hashed_secret`` 只存哈希。API Key 的明文 secret
   **只在创建时返回一次**，之后任何接口都无法再取回。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from loopforge.constants import ID_COLUMN_LENGTH
from loopforge.core.clock import get_clock
from loopforge.core.ids import new_ulid
from loopforge.db.base import Base
from loopforge.db.mixins import utcnow
from loopforge.db.types import JsonType, UtcDateTime

if TYPE_CHECKING:
    pass

__all__ = ["User", "ApiKey", "USER_ROLES"]

#: 用户角色取值（RBAC 三级）。
USER_ROLES: tuple[str, ...] = ("admin", "operator", "viewer")


class User(Base):
    """一个登录用户。"""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, default="viewer", doc="admin / operator / viewer"
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", doc="Argon2 / bcrypt 哈希"
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="noload"
    )

    @property
    def is_admin(self) -> bool:
        """是否为管理员。"""
        return self.role == "admin"


class ApiKey(Base):
    """一把 API Key：``lf_<prefix>_<secret>``，库里只留 prefix 与 secret 哈希。"""

    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("prefix", name="uq_api_keys_prefix"),
        Index("ix_api_keys_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    prefix: Mapped[str] = mapped_column(
        String(32), nullable=False, doc="可公开的短前缀，用于定位记录"
    )
    hashed_secret: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    scopes: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, default=list, doc="权限范围，如 runs:write"
    )
    user_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    user: Mapped[User | None] = relationship(back_populates="api_keys", lazy="joined")

    # -- 领域方法 -----------------------------------------------------------

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """是否已过期；``expires_at`` 为空表示永不过期。"""
        if self.expires_at is None:
            return False
        moment = now if now is not None else get_clock().now()
        return moment >= self.expires_at

    def is_usable(self, *, now: datetime | None = None) -> bool:
        """未吊销且未过期时才可用。"""
        return not self.revoked and not self.is_expired(now=now)
