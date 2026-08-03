"""Alembic 迁移链单测 —— 建表能力与 ORM 模型必须逐字对齐。

为什么这个文件必须存在
----------------------
其余仓储测试都用 ``Base.metadata.create_all`` 快速建表，**绕过了 Alembic**。
于是会出现一种极隐蔽的事故：ORM 模型改了、迁移没跟上，单测全绿，
生产 ``alembic upgrade head`` 却建出一张缺列的表。

本文件用**真实文件库**跑真实迁移链，锁死以下契约
（对应 ``docs/TASK_BREAKDOWN.md`` §3.4 DoD 与 §13.2 迁移链纪律）：

* revision 链 0001 → 0006 **单一 head**，无分叉；
* ``upgrade head`` 建出的表集合 == ORM ``Base.metadata`` 的表集合；
* 关键表的列集合与 ORM 定义一致（抽查并发相关的 ``tasks`` / ``leases``）；
* ``downgrade base`` 能把库清干净（可回滚）；
* 迁移文件本身满足纪律：每个 revision 都有 ``down_revision`` 链、
  文件名与 revision 号一致。

内存库在这里没用：Alembic 自己开连接，``:memory:`` 对它是另一个空库。
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from argparse import Namespace
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import loopforge.models  # noqa: F401 —— 触发全部 ORM 模型注册
from loopforge.db.base import Base
from tests.fixtures.db import create_file_engine, table_names

#: 仓库根目录（``tests/unit/db/test_migrations.py`` 往上四级）。
REPO_ROOT: Path = Path(__file__).resolve().parents[3]

#: 迁移脚本目录。
VERSIONS_DIR: Path = REPO_ROOT / "alembic" / "versions"

#: 期望的 revision 链顺序。
EXPECTED_CHAIN: tuple[str, ...] = ("0001", "0002", "0003", "0004", "0005", "0006")


def _alembic_config(db_url: str) -> Config:
    """构造一份指向临时库的 Alembic 配置。

    **必须走 ``-x db_url=`` 这条通道**：``alembic/env.py`` 的 URL 优先级是
    ``-x db_url=`` > 配置中心（``get_settings().db.url``）> ``alembic.ini``，
    而 ``alembic.ini`` 只在配置中心**加载失败**时才兜底。
    因此单纯 ``set_main_option("sqlalchemy.url", ...)`` 会被配置中心无声覆盖，
    迁移会跑到真实的 ``data/loopforge.db`` 上去，临时库始终是空的。

    Args:
        db_url: 同步（非 async）SQLAlchemy URL；env.py 会自行归一为异步驱动。

    Returns:
        可直接交给 ``command.upgrade`` 的 :class:`alembic.config.Config`。
    """
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    # 保留 ini 值作为最后兜底。
    config.set_main_option("sqlalchemy.url", db_url)
    # env.py 通过 context.get_x_argument() 读取，底层取自 config.cmd_opts.x。
    config.cmd_opts = Namespace(x=[f"db_url={db_url}"])
    return config


# ---------------------------------------------------------------------------
# 迁移链纪律（纯静态检查，不连库）
# ---------------------------------------------------------------------------


def test_migration_chain_has_single_head() -> None:
    """迁移链必须只有一个 head —— 多 head 意味着有人并行加了迁移。"""
    script = ScriptDirectory(str(REPO_ROOT / "alembic"))
    heads = script.get_heads()
    assert len(heads) == 1, f"迁移链分叉了：{heads}"


def test_migration_chain_is_linear_and_ordered() -> None:
    """0001 → 0006 逐级串联，且 base 只有一个起点。"""
    script = ScriptDirectory(str(REPO_ROOT / "alembic"))
    revisions = list(script.walk_revisions())  # head → base
    assert len(revisions) == len(EXPECTED_CHAIN)

    ordered = list(reversed(revisions))  # base → head
    assert [rev.revision for rev in ordered] == list(EXPECTED_CHAIN)

    assert ordered[0].down_revision is None, "0001 必须是链的起点"
    # 相邻配对，长度天然差 1，不能用 strict=True。
    for previous, current in zip(ordered, ordered[1:], strict=False):
        assert current.down_revision == previous.revision, (
            f"{current.revision} 的 down_revision 应为 {previous.revision}"
        )


def test_migration_filenames_match_revision_ids() -> None:
    """文件名前缀必须与 revision 号一致，避免「改了名找不到脚本」。"""
    files = sorted(path.name for path in VERSIONS_DIR.glob("[0-9]*.py"))
    assert len(files) == len(EXPECTED_CHAIN)
    for expected, filename in zip(EXPECTED_CHAIN, files, strict=True):
        assert filename.startswith(f"{expected}_"), f"{filename} 与 {expected} 不匹配"


def test_every_migration_defines_upgrade_and_downgrade() -> None:
    """每个迁移都要能升能降，否则 ``downgrade base`` 会中途卡死。"""
    for path in sorted(VERSIONS_DIR.glob("[0-9]*.py")):
        source = path.read_text(encoding="utf-8")
        assert re.search(r"^def upgrade\(\)", source, re.MULTILINE), f"{path.name} 缺 upgrade"
        assert re.search(r"^def downgrade\(\)", source, re.MULTILINE), (
            f"{path.name} 缺 downgrade"
        )


# ---------------------------------------------------------------------------
# 真实迁移执行（文件库）
# ---------------------------------------------------------------------------


@contextmanager
def _temp_db() -> Iterator[tuple[Path, str]]:
    """开一个独立临时目录承载 SQLite 文件库，退出时自行清理。

    不用 pytest 的 ``tmp_path``：这里只需要一个路径，而 ``tmp_path`` 会为每个
    用例维护编号目录与 ``current`` 符号链接，在参数化场景下产生大量目录轮转。
    """
    directory = Path(tempfile.mkdtemp(prefix="lf-migrations-"))
    db_path = directory / "migrations.db"
    try:
        yield db_path, f"sqlite:///{db_path.as_posix()}"
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _table_set(db_path: Path) -> set[str]:
    """读出 SQLite 文件库里的业务表名集合（剔除内部表与 alembic_version）。"""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    names = {str(row[0]) for row in rows if not str(row[0]).startswith("sqlite_")}
    names.discard("alembic_version")
    return names


@pytest.fixture(scope="module")
def migrated_db() -> Iterator[Path]:
    """模块级共享的「已升到 head」文件库。

    ``upgrade head`` 要跑 6 个 revision、建 31 张表，是本文件最慢的一步。
    只读断言（表覆盖、列覆盖、版本号）没有互相污染的风险，共享一份即可，
    避免每个参数化用例都重跑一遍完整迁移。
    """
    from alembic import command

    with _temp_db() as (db_path, sync_url):
        command.upgrade(_alembic_config(sync_url), "head")
        yield db_path


def test_upgrade_head_creates_all_orm_tables(migrated_db: Path) -> None:
    """``upgrade head`` 建出的表必须覆盖全部 ORM 模型定义的表。"""
    built = _table_set(migrated_db)
    missing = set(Base.metadata.tables) - built
    assert not missing, f"ORM 有模型但迁移没建表：{sorted(missing)}"


def test_upgrade_then_downgrade_is_reversible() -> None:
    """升到 head 再降回 base，业务表应被清空（只剩 alembic 自己的版本表）。

    这个用例会把库拆干净，必须独占一个临时库，不能复用 ``migrated_db``。
    """
    from alembic import command

    with _temp_db() as (db_path, sync_url):
        config = _alembic_config(sync_url)
        command.upgrade(config, "head")
        assert _table_set(db_path), "upgrade head 之后应当有业务表"

        command.downgrade(config, "base")
        remaining = _table_set(db_path)
        assert remaining == set(), f"downgrade base 后仍有残留表：{sorted(remaining)}"


def test_stamped_revision_is_head(migrated_db: Path) -> None:
    """升级完成后 ``alembic_version`` 里记录的应当就是 head。"""
    with sqlite3.connect(migrated_db) as conn:
        current = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert current is not None
    assert current[0] == EXPECTED_CHAIN[-1]


# ---------------------------------------------------------------------------
# 迁移结果 vs ORM 定义（抽查并发关键表）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", ["tasks", "leases", "steps", "events"])
def test_migrated_columns_cover_orm_columns(migrated_db: Path, table_name: str) -> None:
    """并发关键表的列集合必须覆盖 ORM 定义，少一列就是生产事故。"""
    with sqlite3.connect(migrated_db) as conn:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    built_columns = {str(row[1]) for row in rows}
    assert built_columns, f"迁移没有建出 {table_name} 表"

    orm_columns = {column.name for column in Base.metadata.tables[table_name].columns}
    missing = orm_columns - built_columns
    assert not missing, f"{table_name} 迁移缺列：{sorted(missing)}"


async def test_create_file_engine_starts_empty() -> None:
    """``create_file_engine`` 必须给出一个**空库**，迁移测试才有意义。"""
    with _temp_db() as (db_path, _sync_url):
        engine: AsyncEngine = await create_file_engine(db_path.with_name("empty.db"))
        try:
            assert await table_names(engine) == set()
            async with engine.connect() as conn:
                assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
        finally:
            await engine.dispose()
