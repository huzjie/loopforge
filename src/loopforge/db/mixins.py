"""ORM 通用 Mixin：主键、时间戳、乐观锁、软删除、元数据。

约定：

* **所有主键都是 ULID 字符串**（``VARCHAR(26)``），默认值由 Python 侧生成，
  这样 ID 在 flush 之前就可用，方便先建关联再落库；
* **时间戳一律 UTC**，默认值同样走 Python 侧的 :func:`~loopforge.core.clock.get_clock`，
  保证 ``FrozenClock`` 在测试里能生效（``func.now()`` 做不到）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from loopforge.constants import ID_COLUMN_LENGTH
from loopforge.core.clock import get_clock
from loopforge.core.ids import new_ulid
from loopforge.db.types import JsonType, UtcDateTime

__all__ = [
    "IdMixin",
    "TimestampMixin",
    "CreatedAtMixin",
    "VersionMixin",
    "SoftDeleteMixin",
    "MetadataMixin",
    "utcnow",
]


def utcnow() -> datetime:
    """当前 UTC 时间 —— 列 ``default`` 专用。

    走 :func:`~loopforge.core.clock.get_clock`，因此测试注入
    :class:`~loopforge.core.clock.FrozenClock` 后所有默认时间戳都会被冻结。

    Returns:
        tz-aware UTC 时间。
    """
    return get_clock().now()


class IdMixin:
    """ULID 主键。"""

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        primary_key=True,
        default=new_ulid,
        doc="ULID 主键，时间有序、跨库可移植",
    )


class CreatedAtMixin:
    """只需要创建时间的表（append-only 表用）。"""

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        default=utcnow,
        index=True,
        doc="创建时间（UTC）",
    )


class TimestampMixin:
    """创建时间 + 更新时间。"""

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        default=utcnow,
        doc="创建时间（UTC）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        doc="最后更新时间（UTC）",
    )

    def touch(self) -> datetime:
        """手工刷新 ``updated_at``（CAS 路径不走 ORM flush 时用）。

        Returns:
            新的更新时间。
        """
        now = utcnow()
        self.updated_at = now
        return now


class VersionMixin:
    """乐观锁版本列 —— CAS 条件 UPDATE 的核心。

    .. warning::
       ``version`` **不能**用 SQLAlchemy 的 ``version_id_col``。
       我们的 CAS 是显式的 ``UPDATE ... WHERE version = :expected``，
       由 ``task_repo.cas_transition()`` 亲自控制，ORM 自动版本管理会与之打架。
    """

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        doc="乐观锁版本号，每次成功 CAS 迁移 +1",
    )

    def bump_version(self) -> int:
        """本地自增版本号（仅用于内存态对象同步）。

        Returns:
            自增后的版本号。
        """
        self.version = int(self.version) + 1
        return self.version


class SoftDeleteMixin:
    """软删除标记。

    项目里绝大多数表是 append-only 或硬删除（级联），
    只有 ``projects`` / ``repos`` 这类用户资产使用软删除。
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="软删除时间；NULL 表示未删除",
    )

    @property
    def is_deleted(self) -> bool:
        """是否已被软删除。"""
        return self.deleted_at is not None

    def soft_delete(self) -> datetime:
        """执行软删除。

        Returns:
            删除时间戳。
        """
        now = utcnow()
        self.deleted_at = now
        return now

    def restore(self) -> None:
        """撤销软删除。"""
        self.deleted_at = None


class MetadataMixin:
    """自由扩展的 JSON 元数据列。

    列名用 ``meta`` 而不是 ``metadata`` —— 后者是 SQLAlchemy
    :class:`~sqlalchemy.orm.DeclarativeBase` 的保留属性名。
    """

    meta: Mapped[dict[str, Any]] = mapped_column(
        JsonType,
        nullable=False,
        default=dict,
        doc="扩展元数据（JSON）",
    )

    def meta_get(self, key: str, default: Any = None) -> Any:
        """安全读取元数据字段。

        Args:
            key: 字段名。
            default: 缺省值。

        Returns:
            字段值或默认值。
        """
        payload = self.meta or {}
        return payload.get(key, default)

    def meta_set(self, key: str, value: Any) -> None:
        """写入元数据字段（整体替换 dict，触发 ORM 脏标记）。

        Args:
            key: 字段名。
            value: 字段值。
        """
        payload = dict(self.meta or {})
        payload[key] = value
        self.meta = payload


class ActiveMixin:
    """启用/停用开关。"""

    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
        doc="是否启用",
    )
