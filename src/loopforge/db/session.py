"""异步 Session 工厂与作用域管理。

来源：``docs/TASK_BREAKDOWN.md`` §3.1.3。

三种使用姿势：

1. **FastAPI 依赖注入**（WP4）::

       @router.get("/runs")
       async def list_runs(session: AsyncSession = Depends(get_session)):
           ...

2. **脚本 / Worker 内部**::

       async with session_scope() as session:
           await session.execute(...)
       # 正常退出自动 commit，异常自动 rollback

3. **仅读，不想开事务**::

       async with read_session() as session:
           ...

.. note::
   ``expire_on_commit=False`` 是刻意选择：commit 之后 ORM 对象仍可安全读取属性，
   否则在 async 场景下访问已过期属性会触发隐式 IO 并抛
   ``MissingGreenlet``。
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable

from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_scoped_session,
    async_sessionmaker,
)

from loopforge.core.errors import DatabaseError, UniqueViolationError, wrap_exception
from loopforge.db.engine import get_engine

__all__ = [
    "create_session_factory",
    "get_session_factory",
    "set_session_factory",
    "reset_session_factory",
    "get_session",
    "session_scope",
    "read_session",
    "scoped_session_factory",
    "translate_db_error",
]


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_session_factory(
    engine: AsyncEngine | None = None,
    *,
    expire_on_commit: bool = False,
    autoflush: bool = False,
) -> async_sessionmaker[AsyncSession]:
    """基于给定（或全局）引擎创建一个 ``async_sessionmaker``。

    :param engine: 目标引擎；``None`` 表示使用 :func:`loopforge.db.engine.get_engine`。
    :param expire_on_commit: 提交后是否让实例过期。默认 ``False``（见模块 docstring）。
    :param autoflush: 是否在查询前自动 flush。默认 ``False``，避免半成品对象被写库。
    :returns: 全新的 session 工厂。
    """
    target = engine if engine is not None else get_engine()
    return async_sessionmaker(
        bind=target,
        class_=AsyncSession,
        expire_on_commit=expire_on_commit,
        autoflush=autoflush,
        autobegin=True,
    )


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """返回进程级单例 session 工厂（首次调用时惰性创建）。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = create_session_factory()
    return _session_factory


def set_session_factory(
    factory: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    """覆盖全局工厂（测试注入内存库时使用），返回被替换掉的旧工厂。"""
    global _session_factory
    previous = _session_factory
    _session_factory = factory
    return previous if previous is not None else factory


def reset_session_factory() -> None:
    """清空全局工厂，下次 :func:`get_session_factory` 会重新创建。"""
    global _session_factory
    _session_factory = None


def scoped_session_factory() -> async_scoped_session[AsyncSession]:
    """返回按 asyncio Task 隔离的 scoped session（Worker 长驻循环用）。

    :returns: 绑定 :func:`asyncio.current_task` 作用域的 scoped session。
    """
    import asyncio

    return async_scoped_session(get_session_factory(), scopefunc=asyncio.current_task)


# ---------------------------------------------------------------------------
# 错误翻译
# ---------------------------------------------------------------------------


def translate_db_error(exc: SQLAlchemyError) -> DatabaseError:
    """把 SQLAlchemy 异常翻译成 LoopForge 领域异常。

    :param exc: 原始 SQLAlchemy 异常。
    :returns: :class:`~loopforge.core.errors.DatabaseError` 或其子类实例。
    """
    if isinstance(exc, IntegrityError):
        text = str(getattr(exc, "orig", exc))
        lowered = text.lower()
        if "unique" in lowered or "duplicate" in lowered:
            return UniqueViolationError(
                "唯一约束冲突", details={"origin": text[:500]}
            )
        return UniqueViolationError("完整性约束冲突", details={"origin": text[:500]})
    if isinstance(exc, DBAPIError):
        return DatabaseError(
            "数据库驱动错误", details={"origin": str(getattr(exc, "orig", exc))[:500]}
        )
    wrapped = wrap_exception(exc, DatabaseError, message="数据库操作失败")
    assert isinstance(wrapped, DatabaseError)  # noqa: S101 - wrap_exception 契约保证
    return wrapped


# ---------------------------------------------------------------------------
# 作用域
# ---------------------------------------------------------------------------


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：产出一个 session，请求结束时保证关闭。

    该依赖**不自动提交**。写操作请显式 ``await session.commit()``，
    或改用 :func:`session_scope`。

    :yields: 活跃的 :class:`~sqlalchemy.ext.asyncio.AsyncSession`。
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    except SQLAlchemyError as exc:
        await session.rollback()
        raise translate_db_error(exc) from exc
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


@contextlib.asynccontextmanager
async def session_scope(
    factory: Callable[[], AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """事务作用域：正常退出 commit，异常 rollback，最终 close。

    :param factory: 可选的 session 构造器；``None`` 表示用全局工厂。
    :yields: 活跃的 session。
    :raises DatabaseError: SQLAlchemy 异常会被翻译成领域异常再抛出。
    """
    make = factory if factory is not None else get_session_factory()
    session = make()
    try:
        yield session
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise translate_db_error(exc) from exc
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()


@contextlib.asynccontextmanager
async def read_session(
    factory: Callable[[], AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """只读作用域：退出时一律 rollback，永不写库。

    :param factory: 可选的 session 构造器。
    :yields: 活跃的 session。
    """
    make = factory if factory is not None else get_session_factory()
    session = make()
    try:
        yield session
    except SQLAlchemyError as exc:
        raise translate_db_error(exc) from exc
    finally:
        await session.rollback()
        await session.close()
