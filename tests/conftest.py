"""全局 pytest 夹具。

职责
----
1. **路径引导**：把仓库根与 ``src/`` 塞进 ``sys.path``，
   使 ``pytest tests/xxx.py`` 这种绕过 ini 的直连调用也能 import 到包。
2. **全局单例隔离**：Clock / Settings / EventBus / Engine / SessionFactory
   五个进程级单例在每个测试前后强制复位，杜绝测试互相污染。
3. **确定性时间**：默认注入 :class:`~loopforge.core.clock.FrozenClock`，
   任何依赖「现在」的断言都可复现。需要真实时间的测试显式声明 ``system_clock``。
4. 转发 ``tests/fixtures/db.py`` 中的数据库夹具。

时区约定：全部 tz-aware UTC，禁止 naive datetime。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _candidate in (_REPO_ROOT, _REPO_ROOT / "src"):
    _text = str(_candidate)
    if _text not in sys.path:
        sys.path.insert(0, _text)

from loopforge.core.clock import (  # noqa: E402
    Clock,
    FrozenClock,
    SystemClock,
    get_clock,
    set_clock,
)
from loopforge.core.config import Settings, load_settings, set_settings  # noqa: E402
from loopforge.core.event_bus import (  # noqa: E402
    EventBus,
    RecordingSubscriber,
    set_event_bus,
)

# 数据库夹具（db_engine / session_factory / db_session / uow / second_uow）
from tests.fixtures.db import (  # noqa: E402,F401
    MEMORY_URL,
    db_engine,
    db_session,
    second_uow,
    session_factory,
    uow,
)

# Provider 夹具（mock_provider / mock_chat_request / ...）
from tests.fixtures.providers import (  # noqa: E402,F401
    mock_chat_request,
    mock_code_request,
    mock_provider,
    mock_tool_request,
    mock_tool_spec,
)

__all__ = [
    "FROZEN_INSTANT",
    "repo_root",
    "frozen_clock",
    "system_clock",
    "isolated_settings",
    "event_bus",
    "recorder",
    "clean_env",
    # provider fixtures
    "mock_provider",
    "mock_chat_request",
    "mock_code_request",
    "mock_tool_request",
    "mock_tool_spec",
]

#: 全测试统一的「现在」。取一个好记的 UTC 时刻，便于断言。
FROZEN_INSTANT: datetime = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """仓库根目录。"""
    return _REPO_ROOT


@pytest.fixture(autouse=True)
def frozen_clock() -> Iterator[FrozenClock]:
    """默认给每个测试装一个冻结时钟，并在结束后还原系统时钟。

    Yields:
        可用 ``clock.advance(seconds)`` 手工推进的 :class:`FrozenClock`。
    """
    clock = FrozenClock(FROZEN_INSTANT)
    previous: Clock = get_clock()
    set_clock(clock)
    try:
        yield clock
    finally:
        set_clock(previous if not isinstance(previous, FrozenClock) else SystemClock())


@pytest.fixture
def system_clock() -> Iterator[SystemClock]:
    """需要真实时间流逝的测试（如 monotonic 断言）显式覆盖冻结时钟。"""
    clock = SystemClock()
    set_clock(clock)
    try:
        yield clock
    finally:
        set_clock(SystemClock())


@pytest.fixture
def isolated_settings() -> Iterator[Settings]:
    """纯代码默认值构成的配置（不读 YAML / 环境变量 / .env）。

    Yields:
        注入为全局单例的 :class:`Settings`；退出时清空单例。
    """
    settings = load_settings(
        include_default_yaml=False,
        environ={},
        dotenv_file=_REPO_ROOT / "__no_such_dotenv__",
    )
    set_settings(settings)
    try:
        yield settings
    finally:
        set_settings(None)


@pytest.fixture
async def event_bus() -> Any:
    """已启动的事件总线，并设为全局单例；退出时排空 + 停机。"""
    bus = EventBus(queue_size=256)
    set_event_bus(bus)
    await bus.start()
    try:
        yield bus
    finally:
        await bus.stop(drain=True, timeout_s=2.0)
        set_event_bus(None)


@pytest.fixture
def recorder() -> RecordingSubscriber:
    """一个记录全部事件的订阅者。"""
    return RecordingSubscriber()


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """清掉所有 ``LOOPFORGE*`` 环境变量，避免开发机的本地设置干扰断言。"""
    for key in [name for name in os.environ if name.startswith("LOOPFORGE")]:
        monkeypatch.delenv(key, raising=False)
    yield
