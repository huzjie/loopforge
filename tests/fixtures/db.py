"""数据库测试夹具 —— 内存 SQLite + 建表 + UnitOfWork。

设计要点
--------
* **零外部依赖**：默认使用 ``sqlite+aiosqlite:///:memory:``，
  由 :func:`loopforge.db.engine.create_engine_from_settings` 自动挂 ``StaticPool``，
  保证同一测试内所有 session 共享同一条连接（否则内存库会「各建各的表」）。
* **不读全局配置**：显式构造 :class:`~loopforge.core.config.DbSettings`，
  避免单测受 ``config/default.yaml`` / ``.env`` 影响。
* **建表走 ORM metadata**：单测用 ``create_all`` 快速建表；
  迁移正确性由 ``tests/unit/db/test_migrations.py`` 用真实 Alembic 链单独验证。
* **全局单例复位**：每个测试结束后把 ``engine`` / ``session_factory``
  两个进程级单例还原，杜绝测试间串味。

这些夹具通过 ``tests/conftest.py`` 的 ``from tests.fixtures.db import *`` 生效，
测试函数直接声明形参即可使用::

    async def test_something(uow):
        project = await uow.projects.create(slug="demo", name="Demo")
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import loopforge.models  # noqa: F401 —— 触发全部 ORM 模型注册到 metadata
from loopforge.core.config import DbSettings
from loopforge.db.base import Base
from loopforge.db.engine import create_engine_from_settings, set_engine
from loopforge.db.session import (
    create_session_factory,
    reset_session_factory,
    set_session_factory,
)
from loopforge.db.uow import UnitOfWork

__all__ = [
    "MEMORY_URL",
    "make_db_settings",
    "create_memory_engine",
    "create_file_engine",
    "table_names",
    "db_engine",
    "session_factory",
    "db_session",
    "uow",
    "second_uow",
]

#: 内存库 URL —— 走 StaticPool，全测试共享一条连接。
MEMORY_URL: Final[str] = "sqlite+aiosqlite:///:memory:"


def make_db_settings(url: str = MEMORY_URL) -> DbSettings:
    """构造一份「与全局配置无关」的测试用数据库配置。

    Args:
        url: 数据库 URL。

    Returns:
        关闭 echo、连接池收敛到最小的 :class:`DbSettings`。
    """
    return DbSettings(
        url=url,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=False,
        echo=False,
    )


async def create_memory_engine(url: str = MEMORY_URL) -> AsyncEngine:
    """创建内存库引擎并按 ORM metadata 建好全部表。

    Args:
        url: 数据库 URL，默认内存库。

    Returns:
        已建表、可直接使用的 :class:`AsyncEngine`。
    """
    engine = create_engine_from_settings(make_db_settings(url), url=url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


async def create_file_engine(path: Path) -> AsyncEngine:
    """创建基于磁盘文件的 SQLite 引擎（**不建表**）。

    Alembic 迁移测试需要一个真实文件库：内存库在 Alembic 自己开的连接里
    是另一个空库，无法验证迁移结果。

    Args:
        path: SQLite 文件路径，父目录会被自动创建。

    Returns:
        空库引擎。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    return create_engine_from_settings(make_db_settings(url), url=url)


async def table_names(engine: AsyncEngine) -> set[str]:
    """读取 SQLite 库中的用户表名集合。

    Args:
        engine: 目标引擎。

    Returns:
        表名集合（已排除 ``sqlite_`` 内部表）。
    """
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table'")
        )
        return {row[0] for row in result if not str(row[0]).startswith("sqlite_")}


# ---------------------------------------------------------------------------
# pytest 夹具
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """函数级内存库引擎，并把它设为进程级单例。"""
    engine = await create_memory_engine()
    set_engine(engine)
    try:
        yield engine
    finally:
        set_engine(None)
        await engine.dispose()


@pytest.fixture
async def session_factory(
    db_engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """绑定到内存库的 session 工厂，并设为进程级单例。"""
    factory = create_session_factory(db_engine)
    set_session_factory(factory)
    try:
        yield factory
    finally:
        reset_session_factory()


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """裸 session（需要直接写 SQL 的测试用）。"""
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest.fixture
async def uow(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[UnitOfWork]:
    """已进入上下文的 UnitOfWork（退出时自动 commit）。"""
    async with UnitOfWork(session_factory=session_factory) as unit:
        yield unit


@pytest.fixture
async def second_uow(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[UnitOfWork]:
    """第二个独立事务，用来模拟「两个 Worker 并发抢同一行」。"""
    async with UnitOfWork(session_factory=session_factory) as unit:
        yield unit
