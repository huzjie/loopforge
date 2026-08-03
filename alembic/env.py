"""Alembic 运行环境 —— 异步引擎 + 配置中心统一取 URL。

设计要点
--------

1. **URL 的唯一真源是 :func:`loopforge.core.config.get_settings`**，
   ``alembic.ini`` 里的 ``sqlalchemy.url`` 只是命令行兜底。
   这样 ``LF_DB__URL=postgresql+asyncpg://...  alembic upgrade head`` 就能直接生效，
   不需要改配置文件。
2. **异步引擎**：SQLAlchemy 2.0 的 ``AsyncEngine`` 不能直接喂给 Alembic，
   必须用 ``connection.run_sync(...)`` 把同步的迁移逻辑套进去。
3. **SQLite 批处理模式**：SQLite 不支持 ``ALTER TABLE DROP COLUMN`` 等操作，
   ``render_as_batch=True`` 让 Alembic 自动改写成「建新表 → 拷数据 → 换名」。
4. **禁止 autogenerate**：迁移链由 WP1 owner 手写，``compare_type`` 之类的开关
   保留只是为了 ``alembic check`` 能给出人工核对用的 diff。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# 让 `alembic` 命令在未安装包的情况下也能找到 src/ 布局下的 loopforge。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _PROJECT_ROOT / "src"
if _SRC_DIR.is_dir():
    import sys

    if str(_SRC_DIR) not in sys.path:
        sys.path.insert(0, str(_SRC_DIR))

from loopforge.core.config import get_settings  # noqa: E402
from loopforge.db.base import Base  # noqa: E402
from loopforge.db.engine import is_sqlite_url, normalize_async_url  # noqa: E402
import loopforge.models  # noqa: E402,F401 —— 触发全部 ORM 模型注册到 metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

#: Alembic 比对用的目标元数据（同时提供命名约定给 op.create_table）。
target_metadata = Base.metadata


def _resolve_url() -> str:
    """决定本次迁移使用的数据库 URL。

    优先级：``-x db_url=...`` > 配置中心 > ``alembic.ini``。

    Returns:
        规范化后的异步驱动 URL。
    """
    x_args: dict[str, str] = context.get_x_argument(as_dictionary=True)
    override = x_args.get("db_url", "").strip()
    if override:
        return normalize_async_url(override)
    try:
        return normalize_async_url(get_settings().db.url)
    except Exception:  # noqa: BLE001 —— 配置加载失败时退回 ini，保证迁移可用
        fallback = config.get_main_option("sqlalchemy.url") or ""
        if not fallback:
            raise
        return normalize_async_url(fallback)


def _common_options(url: str) -> dict[str, Any]:
    """返回离线/在线模式共用的 ``context.configure`` 选项。

    Args:
        url: 目标数据库 URL。

    Returns:
        选项字典。
    """
    return {
        "target_metadata": target_metadata,
        "compare_type": True,
        "compare_server_default": True,
        "render_as_batch": is_sqlite_url(url),
        "transaction_per_migration": True,
        "include_schemas": False,
    }


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连接数据库。"""
    url = _resolve_url()
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_common_options(url),
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    """在同步连接上真正执行迁移。

    Args:
        connection: ``run_sync`` 提供的同步连接代理。
    """
    url = str(connection.engine.url)
    context.configure(connection=connection, **_common_options(url))
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    """建立异步引擎并驱动迁移。"""
    url = _resolve_url()
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url

    if is_sqlite_url(url):
        # SQLite 文件所在目录可能还不存在（首次 upgrade head）。
        db_path = url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(_do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式入口。

    已有事件循环时（例如从测试的 async 上下文里调用）走 ``asyncio.run`` 会炸，
    这里做一次兜底：检测到运行中的循环就退回同步引擎路径。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_run_async_migrations())
        return

    # 已在事件循环中：用独立线程跑一个全新的循环，避免 "cannot be called from a running loop"。
    import threading

    error: list[BaseException] = []

    def _worker() -> None:
        try:
            asyncio.run(_run_async_migrations())
        except BaseException as exc:  # noqa: BLE001 —— 需要跨线程回传
            error.append(exc)

    thread = threading.Thread(target=_worker, name="alembic-migrations", daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
