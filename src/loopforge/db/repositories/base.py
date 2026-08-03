"""泛型仓储基类。

来源：``docs/TASK_BREAKDOWN.md`` §3.1.3。

设计取舍：

* 仓储**只负责持久化**，不发事件、不做业务校验 —— 那是 Service 层（WP3）的事；
* 仓储**不自己 commit** —— 事务边界由 :class:`~loopforge.db.uow.UnitOfWork`
  统一掌管，否则「事务边界与事件边界对齐」就无从保证；
* 所有查询默认**不加载关系**（``lazy="noload"``），需要时由调用方显式
  ``selectinload``，避免 async 场景下的隐式 IO。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from loopforge.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from loopforge.core.errors import NotFoundError
from loopforge.db.base import Base

__all__ = ["BaseRepository", "ModelT", "clamp_limit"]

ModelT = TypeVar("ModelT", bound=Base)


def clamp_limit(limit: int | None) -> int:
    """把分页大小夹到 ``[1, MAX_PAGE_SIZE]``。

    :param limit: 调用方给的分页大小；``None`` 用 :data:`~loopforge.constants.DEFAULT_PAGE_SIZE`。
    :returns: 合法的分页大小。
    """
    if limit is None:
        return DEFAULT_PAGE_SIZE
    if limit < 1:
        return 1
    if limit > MAX_PAGE_SIZE:
        return MAX_PAGE_SIZE
    return limit


class BaseRepository(Generic[ModelT]):
    """通用 CRUD 仓储。

    子类只需声明 ``model`` 类属性::

        class ProjectRepository(BaseRepository[Project]):
            model = Project
    """

    #: 子类必须覆盖为具体 ORM 模型类。
    model: type[ModelT]

    #: 主键属性名。``events`` 用 ``seq``、``idempotency_records`` 用 ``key``，
    #: 其余一律 ``id``。
    pk_field: str = "id"

    def __init__(self, session: AsyncSession) -> None:
        """:param session: 由 UoW 或依赖注入提供的活跃 session。"""
        self.session = session

    # -- 内部工具 -----------------------------------------------------------

    @property
    def _pk(self) -> Any:
        """返回主键 InstrumentedAttribute。"""
        return getattr(self.model, self.pk_field)

    def _base_select(self) -> Select[tuple[ModelT]]:
        """返回不带任何过滤条件的基础 SELECT。"""
        return select(self.model)

    # -- 写 -----------------------------------------------------------------

    def add(self, instance: ModelT) -> ModelT:
        """把实例加入 session（不 flush、不 commit）。"""
        self.session.add(instance)
        return instance

    async def create(self, **values: Any) -> ModelT:
        """构造实例、加入 session 并 flush（拿到默认值与自增主键）。

        :param values: 模型字段键值对。
        :returns: 已 flush 的实例。
        """
        instance = self.model(**values)
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def bulk_create(self, rows: Sequence[dict[str, Any]]) -> list[ModelT]:
        """批量创建。

        :param rows: 字段字典序列。
        :returns: 已 flush 的实例列表（顺序与入参一致）。
        """
        instances = [self.model(**row) for row in rows]
        if not instances:
            return []
        self.session.add_all(instances)
        await self.session.flush()
        return instances

    async def update_by_pk(self, pk: Any, **values: Any) -> int:
        """按主键做条件 UPDATE。

        :returns: 受影响行数（0 表示主键不存在）。
        """
        if not values:
            return 0
        stmt = update(self.model).where(self._pk == pk).values(**values)
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def delete_by_pk(self, pk: Any) -> int:
        """按主键物理删除。

        :returns: 受影响行数。
        """
        stmt = delete(self.model).where(self._pk == pk)
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def flush(self) -> None:
        """把挂起变更刷到数据库（仍在事务内）。"""
        await self.session.flush()

    # -- 读 -----------------------------------------------------------------

    async def get(self, pk: Any) -> ModelT | None:
        """按主键取一条；不存在返回 ``None``。"""
        stmt = self._base_select().where(self._pk == pk)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_raise(self, pk: Any) -> ModelT:
        """按主键取一条；不存在抛 :class:`~loopforge.core.errors.NotFoundError`。

        :raises NotFoundError: 记录不存在。
        """
        found = await self.get(pk)
        if found is None:
            raise NotFoundError(
                f"{self.model.__tablename__} 不存在",
                details={"table": self.model.__tablename__, "pk": str(pk)},
            )
        return found

    async def exists(self, pk: Any) -> bool:
        """主键是否存在。"""
        stmt = select(func.count()).select_from(self.model).where(self._pk == pk)
        result = await self.session.execute(stmt)
        return bool(result.scalar_one())

    async def find_one_by(self, **filters: Any) -> ModelT | None:
        """按等值条件取第一条。"""
        stmt = self._base_select()
        for field, value in filters.items():
            stmt = stmt.where(getattr(self.model, field) == value)
        result = await self.session.execute(stmt.limit(1))
        return result.scalars().first()

    async def list_by(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        order_by: Any = None,
        **filters: Any,
    ) -> list[ModelT]:
        """按等值条件分页查询。

        :param limit: 分页大小，自动夹在 ``[1, MAX_PAGE_SIZE]``。
        :param offset: 偏移量，负值按 0 处理。
        :param order_by: 排序表达式；``None`` 时按主键升序。
        :param filters: 等值过滤条件。
        """
        stmt = self._base_select()
        for field, value in filters.items():
            stmt = stmt.where(getattr(self.model, field) == value)
        stmt = stmt.order_by(order_by if order_by is not None else self._pk)
        stmt = stmt.offset(max(0, offset)).limit(clamp_limit(limit))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by(self, **filters: Any) -> int:
        """按等值条件计数。"""
        stmt = select(func.count()).select_from(self.model)
        for field, value in filters.items():
            stmt = stmt.where(getattr(self.model, field) == value)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def all(self, *, limit: int | None = None, offset: int = 0) -> list[ModelT]:
        """无过滤条件的分页全量查询。"""
        return await self.list_by(limit=limit, offset=offset)
