"""异步引擎工厂 —— SQLite（aiosqlite）与 PostgreSQL（asyncpg）双分支。

SQLite 上必须设置的三个 PRAGMA（``ARCHITECTURE.md`` §5.2 脚注）::

    PRAGMA journal_mode = WAL       # 读写不互斥，Worker 并发争抢租约的前提
    PRAGMA busy_timeout = 5000      # 写锁冲突时等 5 秒而不是立刻 SQLITE_BUSY
    PRAGMA foreign_keys = ON        # SQLite 默认关闭外键，不开就没有级联删除

这三条在 ``connect`` 事件里统一注入，**任何绕过本模块自建 engine 的代码都会丢掉它们**。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from loopforge.core.config import DbSettings, get_settings
from loopforge.core.errors import ConfigError, DatabaseError
from loopforge.core.logging import get_logger

__all__ = [
    "create_engine_from_settings",
    "get_engine",
    "set_engine",
    "dispose_engine",
    "ping",
    "dialect_of",
    "is_sqlite_url",
    "is_postgres_url",
    "normalize_async_url",
    "SUPPORTED_DIALECTS",
]

_logger = get_logger(__name__)

#: 支持的方言。
SUPPORTED_DIALECTS: Final[frozenset[str]] = frozenset({"sqlite", "postgresql"})

#: 同步 URL → 异步 URL 的驱动映射。
_ASYNC_DRIVER_MAP: Final[dict[str, str]] = {
    "sqlite": "sqlite+aiosqlite",
    "postgresql": "postgresql+asyncpg",
    "postgres": "postgresql+asyncpg",
    "postgresql+psycopg2": "postgresql+asyncpg",
    "postgresql+psycopg": "postgresql+asyncpg",
}

#: 内存库 URL 特征。
_MEMORY_RE: Final[re.Pattern[str]] = re.compile(r":memory:|mode=memory")


def is_sqlite_url(url: str) -> bool:
    """判断是否 SQLite URL。

    Args:
        url: 数据库 URL。

    Returns:
        是 SQLite 返回 ``True``。
    """
    return make_url(url).get_backend_name() == "sqlite"


def is_postgres_url(url: str) -> bool:
    """判断是否 PostgreSQL URL。

    Args:
        url: 数据库 URL。

    Returns:
        是 PostgreSQL 返回 ``True``。
    """
    return make_url(url).get_backend_name() == "postgresql"


def dialect_of(url: str) -> str:
    """取出 URL 的方言名。

    Args:
        url: 数据库 URL。

    Returns:
        ``sqlite`` 或 ``postgresql``。

    Raises:
        ConfigError: 方言不受支持。
    """
    backend = make_url(url).get_backend_name()
    if backend not in SUPPORTED_DIALECTS:
        raise ConfigError(
            f"不支持的数据库方言 {backend!r}，仅支持 {sorted(SUPPORTED_DIALECTS)}",
            details={"url_backend": backend},
        )
    return backend


def normalize_async_url(url: str) -> str:
    """把同步 URL 规范化成异步驱动 URL。

    ``sqlite:///x.db`` → ``sqlite+aiosqlite:///x.db``；
    ``postgresql://…`` → ``postgresql+asyncpg://…``。

    Args:
        url: 原始 URL。

    Returns:
        异步驱动 URL。

    Raises:
        ConfigError: 方言不受支持。
    """
    parsed = make_url(url)
    backend = parsed.get_backend_name()
    if backend not in SUPPORTED_DIALECTS:
        raise ConfigError(
            f"不支持的数据库方言 {backend!r}", details={"url_backend": backend}
        )
    driver = parsed.get_driver_name()
    if driver in {"aiosqlite", "asyncpg"}:
        return url
    target = _ASYNC_DRIVER_MAP.get(f"{backend}+{driver}") or _ASYNC_DRIVER_MAP.get(backend)
    if target is None:  # pragma: no cover —— 上面的 SUPPORTED_DIALECTS 已兜住
        raise ConfigError(f"无法为 {url!r} 推导异步驱动", details={"url": url})
    return str(parsed.set(drivername=target))


def _ensure_sqlite_dir(url: str) -> None:
    """确保 SQLite 文件所在目录存在（Windows 友好，全程 pathlib）。

    Args:
        url: SQLite URL。
    """
    parsed = make_url(url)
    database = parsed.database
    if not database or _MEMORY_RE.search(database):
        return
    path = Path(database)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)


def _register_sqlite_pragmas(engine: AsyncEngine, settings: DbSettings) -> None:
    """给 SQLite 连接注册 PRAGMA。

    Args:
        engine: 异步引擎。
        settings: 数据库配置。
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        """连接建立时设置 PRAGMA。"""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA journal_mode={settings.sqlite_journal_mode}")
            cursor.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def _register_postgres_settings(engine: AsyncEngine, settings: DbSettings) -> None:
    """给 PostgreSQL 连接设置语句超时。

    Args:
        engine: 异步引擎。
        settings: 数据库配置。
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        """连接建立时设置 statement_timeout。"""
        timeout_ms = settings.statement_timeout_s * 1000
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"SET statement_timeout = {timeout_ms}")
        finally:
            cursor.close()


def create_engine_from_settings(
    settings: DbSettings | None = None,
    *,
    url: str | None = None,
    echo: bool | None = None,
    poolclass: Any = None,
    connect_args: Mapping[str, Any] | None = None,
) -> AsyncEngine:
    """按配置创建异步引擎。

    Args:
        settings: 数据库配置；``None`` 时取全局 :func:`get_settings`。
        url: 覆盖配置里的 URL（测试常用）。
        echo: 覆盖 SQL 回显开关。
        poolclass: 覆盖连接池类；SQLite 内存库自动用 :class:`StaticPool`。
        connect_args: 追加的驱动参数。

    Returns:
        配置好的 :class:`AsyncEngine`。

    Raises:
        ConfigError: URL 方言不受支持。
        DatabaseError: 引擎创建失败。
    """
    db_settings = settings if settings is not None else get_settings().db
    raw_url = url if url is not None else db_settings.url
    async_url = normalize_async_url(raw_url)
    backend = dialect_of(async_url)
    kwargs: dict[str, Any] = {
        "echo": db_settings.echo if echo is None else echo,
        "future": True,
        "pool_pre_ping": db_settings.pool_pre_ping,
    }
    driver_args: dict[str, Any] = dict(connect_args or {})

    if backend == "sqlite":
        _ensure_sqlite_dir(async_url)
        is_memory = bool(_MEMORY_RE.search(async_url))
        # SQLite 不支持真正的连接池并发写，内存库必须共用同一连接。
        kwargs["poolclass"] = poolclass or (StaticPool if is_memory else NullPool)
        kwargs.pop("pool_pre_ping", None)
        driver_args.setdefault("timeout", db_settings.sqlite_busy_timeout_ms / 1000)
        if is_memory:
            driver_args.setdefault("check_same_thread", False)
    else:
        kwargs["pool_size"] = db_settings.pool_size
        kwargs["max_overflow"] = db_settings.max_overflow
        kwargs["pool_recycle"] = db_settings.pool_recycle_s
        if poolclass is not None:
            kwargs["poolclass"] = poolclass
            kwargs.pop("pool_size", None)
            kwargs.pop("max_overflow", None)

    if driver_args:
        kwargs["connect_args"] = driver_args

    try:
        engine = create_async_engine(async_url, **kwargs)
    except SQLAlchemyError as exc:
        raise DatabaseError(
            f"创建数据库引擎失败：{exc}", details={"url_backend": backend}
        ) from exc

    if backend == "sqlite":
        _register_sqlite_pragmas(engine, db_settings)
    else:
        _register_postgres_settings(engine, db_settings)

    _logger.debug("db.engine.created", backend=backend, echo=kwargs["echo"])
    return engine


# ---------------------------------------------------------------------------
# 进程级单例
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """取得进程级引擎单例（懒加载）。

    Returns:
        :class:`AsyncEngine` 单例。
    """
    global _engine  # noqa: PLW0603 —— 单例模式必须
    if _engine is None:
        _engine = create_engine_from_settings()
    return _engine


def set_engine(engine: AsyncEngine | None) -> None:
    """替换或清空引擎单例（测试与启动装配用）。

    Args:
        engine: 新引擎；``None`` 表示清空。
    """
    global _engine  # noqa: PLW0603 —— 单例模式必须
    _engine = engine


async def dispose_engine() -> None:
    """释放引擎连接池并清空单例。"""
    global _engine  # noqa: PLW0603 —— 单例模式必须
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def ping(engine: AsyncEngine | None = None) -> bool:
    """健康检查：执行 ``SELECT 1``。

    Args:
        engine: 目标引擎；``None`` 时用单例。

    Returns:
        连通返回 ``True``。

    Raises:
        DatabaseError: 连接失败。
    """
    target = engine if engine is not None else get_engine()
    try:
        async with target.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            return result.scalar_one() == 1
    except SQLAlchemyError as exc:
        raise DatabaseError(f"数据库连接失败：{exc}") from exc


def sync_engine_of(engine: AsyncEngine) -> Engine:
    """取出异步引擎底层的同步引擎（Alembic 与事件监听用）。

    Args:
        engine: 异步引擎。

    Returns:
        同步引擎。
    """
    return engine.sync_engine
