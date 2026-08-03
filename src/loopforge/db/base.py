"""SQLAlchemy 2.0 声明式基类与命名约定。

**命名约定必须在建表前生效**：Alembic 的 ``0006_indexes_and_constraints`` 依赖
自动生成的约束名来做 ``drop_constraint``，一旦约定变更，历史迁移就无法回滚。
因此本文件与 ``models/`` 一样由 WP1 owner 独家维护。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, declared_attr

__all__ = ["Base", "NAMING_CONVENTION", "metadata"]

#: 索引与约束的命名模板 —— 与 ``ARCHITECTURE.md`` §5.1 的 ``ix_/uq_/ck_/fk_/pk_`` 前缀一致。
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: 全局 MetaData —— Alembic ``env.py`` 的 ``target_metadata``。
metadata: MetaData = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。

    统一提供：

    * 共享 :data:`metadata`（含命名约定）；
    * ``__tablename__`` 缺省不自动推导 —— **强制每个模型显式声明表名**，
      避免驼峰转换歧义；
    * 通用的 :meth:`to_dict` 与 :meth:`__repr__`。
    """

    metadata = metadata

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805 —— SQLAlchemy declared_attr 约定
        """强制子类显式声明表名。

        Returns:
            永不返回。

        Raises:
            NotImplementedError: 子类未声明 ``__tablename__``。
        """
        raise NotImplementedError(
            f"{cls.__name__} 必须显式声明 __tablename__（禁止自动推导，见 db/base.py）"
        )

    def to_dict(self, *, exclude: frozenset[str] = frozenset()) -> dict[str, Any]:
        """把 ORM 实例转成字典。

        Args:
            exclude: 需要排除的列名集合（如敏感列）。

        Returns:
            列名 → 值 的字典。
        """
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
            if column.name not in exclude
        }

    def __repr__(self) -> str:
        """带主键的调试表示。

        Returns:
            形如 ``<Run id='01J...' state='RUNNING'>`` 的字符串。
        """
        primary_keys = [column.name for column in self.__table__.primary_key.columns]
        parts = [f"{name}={getattr(self, name, None)!r}" for name in primary_keys]
        state = getattr(self, "state", None)
        if state is not None:
            parts.append(f"state={state!r}")
        return f"<{type(self).__name__} {' '.join(parts)}>"
