"""数据访问层聚合导出。

来源：``docs/ARCHITECTURE.md`` §5、``docs/TASK_BREAKDOWN.md`` §3.1.3。

分层关系::

    engine.py    → AsyncEngine（方言归一化、SQLite PRAGMA、连接池）
      ↓
    session.py   → async_sessionmaker + get_session / session_scope
      ↓
    uow.py       → UnitOfWork（事务边界 + 事件延后发布 + 仓储聚合）
      ↓
    repositories → 具体仓储（CAS / append-only / 幂等 upsert）

    base.py / types.py / mixins.py 提供声明式基座；
    seed.py 提供首次启动的幂等种子数据。

下游 WP 最常用的三个入口：

* ``async with UnitOfWork() as uow:`` —— 业务写路径；
* ``async for session in get_session():`` —— FastAPI 依赖注入（WP5）；
* ``async with read_session() as session:`` —— 只读查询，永不提交。
"""

from __future__ import annotations

from loopforge.db.base import NAMING_CONVENTION, Base, metadata
from loopforge.db.engine import (
    SUPPORTED_DIALECTS,
    create_engine_from_settings,
    dialect_of,
    dispose_engine,
    get_engine,
    is_postgres_url,
    is_sqlite_url,
    normalize_async_url,
    ping,
    set_engine,
)
from loopforge.db.mixins import (
    ActiveMixin,
    CreatedAtMixin,
    IdMixin,
    MetadataMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
    utcnow,
)
from loopforge.db.session import (
    create_session_factory,
    get_session,
    get_session_factory,
    read_session,
    reset_session_factory,
    session_scope,
    set_session_factory,
    translate_db_error,
)
from loopforge.db.types import (
    MONEY_ZERO,
    DecimalMoney,
    IdColumnType,
    JsonType,
    StrEnumType,
    ULIDType,
    UtcDateTime,
)
from loopforge.db.uow import PendingEvent, UnitOfWork, transactional

__all__ = [
    # 声明式基座
    "Base",
    "metadata",
    "NAMING_CONVENTION",
    # 列类型
    "JsonType",
    "UtcDateTime",
    "StrEnumType",
    "DecimalMoney",
    "IdColumnType",
    "ULIDType",
    "MONEY_ZERO",
    # Mixin
    "IdMixin",
    "TimestampMixin",
    "CreatedAtMixin",
    "VersionMixin",
    "SoftDeleteMixin",
    "MetadataMixin",
    "ActiveMixin",
    "utcnow",
    # 引擎
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
    # 会话
    "create_session_factory",
    "get_session_factory",
    "set_session_factory",
    "reset_session_factory",
    "get_session",
    "session_scope",
    "read_session",
    "translate_db_error",
    # 事务
    "UnitOfWork",
    "PendingEvent",
    "transactional",
]
