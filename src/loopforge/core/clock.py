"""时间抽象 —— **全仓唯一的时间来源**。

冻结签名（``TASK_BREAKDOWN.md`` §11.2）::

    class Clock(Protocol):
        def now(self) -> datetime: ...      # 必须是 tz-aware UTC
        def monotonic(self) -> float: ...

铁律
----
**任何模块要拿当前时间，必须注入 ``Clock`` 依赖，禁止直接调用
``datetime.now()`` / ``time.time()``。** 原因：

1. 长程 Agent 的租约、超时、预算窗口全都基于时间，测试必须能「冻结时间」；
2. 崩溃恢复的对账逻辑要能重放历史时刻；
3. CI 有 ``scripts/check_clock_usage.py`` 静态扫描该违规。

本模块自身是唯一豁免点。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

__all__ = [
    "Clock",
    "SystemClock",
    "FrozenClock",
    "OffsetClock",
    "get_clock",
    "set_clock",
    "ensure_utc",
    "to_iso8601",
    "from_iso8601",
]


@runtime_checkable
class Clock(Protocol):
    """时间源协议。"""

    def now(self) -> datetime:
        """返回当前时刻，**必须是 tz-aware 的 UTC ``datetime``**。"""
        ...

    def monotonic(self) -> float:
        """返回单调递增的秒数，仅用于测量时间差，不可用于绝对时刻。"""
        ...


class SystemClock:
    """真实系统时钟（生产默认实现）。"""

    __slots__ = ()

    def now(self) -> datetime:
        """当前 UTC 时刻。"""
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """进程单调时钟。"""
        return time.monotonic()

    def __repr__(self) -> str:
        return "SystemClock()"


class FrozenClock:
    """可冻结 / 可手工推进的时钟（测试专用）。

    >>> clk = FrozenClock(datetime(2026, 8, 3, tzinfo=UTC))
    >>> clk.now().isoformat()
    '2026-08-03T00:00:00+00:00'
    >>> _ = clk.advance(90)
    >>> clk.now().isoformat()
    '2026-08-03T00:01:30+00:00'
    """

    __slots__ = ("_instant", "_monotonic")

    def __init__(self, instant: datetime | None = None, *, monotonic: float = 0.0) -> None:
        """:param instant: 初始时刻，缺省为 ``2026-01-01T00:00:00Z``。"""
        base = instant if instant is not None else datetime(2026, 1, 1, tzinfo=UTC)
        self._instant: datetime = ensure_utc(base)
        self._monotonic: float = monotonic

    def now(self) -> datetime:
        """返回被冻结的时刻。"""
        return self._instant

    def monotonic(self) -> float:
        """返回被冻结的单调值。"""
        return self._monotonic

    def advance(self, seconds: float) -> datetime:
        """向前推进 ``seconds`` 秒，返回推进后的时刻。"""
        self._instant = self._instant + timedelta(seconds=seconds)
        self._monotonic += seconds
        return self._instant

    def set(self, instant: datetime) -> datetime:
        """直接设定时刻，返回设定后的时刻。"""
        self._instant = ensure_utc(instant)
        return self._instant

    def __repr__(self) -> str:
        return f"FrozenClock({self._instant.isoformat()})"


class OffsetClock:
    """带固定偏移的时钟 —— 用于模拟「机器时钟漂移」场景的租约测试。"""

    __slots__ = ("_base", "_offset_s")

    def __init__(self, base: Clock, offset_s: float) -> None:
        """:param offset_s: 相对 ``base`` 的偏移秒数，可为负。"""
        self._base = base
        self._offset_s = offset_s

    def now(self) -> datetime:
        """基准时钟加偏移。"""
        return self._base.now() + timedelta(seconds=self._offset_s)

    def monotonic(self) -> float:
        """基准单调值加偏移。"""
        return self._base.monotonic() + self._offset_s

    def __repr__(self) -> str:
        return f"OffsetClock(base={self._base!r}, offset_s={self._offset_s})"


_DEFAULT_CLOCK: Clock = SystemClock()


def get_clock() -> Clock:
    """返回进程级默认时钟。

    优先使用依赖注入；只有在实在拿不到注入实例的场合（如模块级默认参数）
    才回退到本函数。
    """
    return _DEFAULT_CLOCK


def set_clock(clock: Clock) -> Clock:
    """替换进程级默认时钟，返回被替换掉的旧时钟（便于测试还原）。"""
    global _DEFAULT_CLOCK  # noqa: PLW0603
    previous = _DEFAULT_CLOCK
    _DEFAULT_CLOCK = clock
    return previous


def ensure_utc(value: datetime) -> datetime:
    """把 ``datetime`` 归一化为 tz-aware UTC。

    * naive datetime 视为 UTC 并补上时区；
    * 带其他时区的转换到 UTC。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_iso8601(value: datetime) -> str:
    """转成 API 契约要求的 ISO 8601 UTC 字符串（毫秒精度 + ``Z``）。

    >>> to_iso8601(datetime(2026, 8, 3, 12, 34, 56, 789000, tzinfo=UTC))
    '2026-08-03T12:34:56.789Z'
    """
    normalized = ensure_utc(value)
    millis = normalized.microsecond // 1000
    return f"{normalized.strftime('%Y-%m-%dT%H:%M:%S')}.{millis:03d}Z"


def from_iso8601(text: str) -> datetime:
    """解析 ISO 8601 字符串为 tz-aware UTC ``datetime``。

    兼容结尾的 ``Z`` 与显式偏移量。
    """
    candidate = text.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    return ensure_utc(datetime.fromisoformat(candidate))
