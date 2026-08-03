"""ID 生成 —— ULID（时间有序）与追踪 ID。

冻结签名（``TASK_BREAKDOWN.md`` §11.2）::

    def new_ulid() -> str: ...
    def new_trace_id() -> str: ...

为什么用 ULID 而不是 UUID4
--------------------------
1. **时间有序**：26 位 Crockford Base32，字典序 == 时间序，B-Tree 索引友好，
   分页游标可以直接用主键，不需要额外排序列；
2. **跨库可移植**：纯 ASCII，``VARCHAR(26)`` 在 SQLite / PostgreSQL 表现一致；
3. **可读**：日志里肉眼即可判断先后。

本模块零第三方依赖（不引 ``python-ulid``），自带实现约 60 行。

确定性模式
----------
测试与 MockProvider 需要「同样输入产出同样 ID」，用 :func:`seeded_generator`
或 :func:`deterministic_ulid` 即可，不影响生产路径。
"""

from __future__ import annotations

import os
import random
import threading
from collections.abc import Iterator

from loopforge.core.clock import Clock, get_clock
from loopforge.core.hashing import sha256_hex

__all__ = [
    "ULID_LENGTH",
    "CROCKFORD_ALPHABET",
    "new_ulid",
    "new_trace_id",
    "short_id",
    "deterministic_ulid",
    "seeded_generator",
    "is_ulid",
    "ulid_timestamp_ms",
]

#: ULID 字符串长度（固定 26）。
ULID_LENGTH = 26

#: Crockford Base32 字母表（去掉了 I / L / O / U 以避免误读）。
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_ENCODE_LEN_TIME = 10
_ENCODE_LEN_RANDOM = 16
_DECODE_MAP: dict[str, int] = {ch: idx for idx, ch in enumerate(CROCKFORD_ALPHABET)}

_lock = threading.Lock()
_last_timestamp_ms = -1
_last_randomness = 0
_MAX_RANDOMNESS = (1 << 80) - 1


def _encode(value: int, length: int) -> str:
    """把整数编码成定长 Crockford Base32 字符串。"""
    chars: list[str] = []
    for _ in range(length):
        chars.append(CROCKFORD_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def _decode(text: str) -> int:
    """把 Crockford Base32 字符串解码成整数。"""
    total = 0
    for ch in text:
        total = (total << 5) | _DECODE_MAP[ch.upper()]
    return total


def new_ulid(*, clock: Clock | None = None) -> str:
    """生成一个新的 ULID。

    同一毫秒内多次调用时，随机段按「单调递增」方式 +1，
    保证**严格递增**（ULID 规范的 monotonic 模式），避免同毫秒内乱序。

    :param clock: 时间源；缺省使用进程默认时钟。
    :return: 26 位大写 ULID 字符串。
    """
    global _last_timestamp_ms, _last_randomness  # noqa: PLW0603

    active_clock = clock if clock is not None else get_clock()
    timestamp_ms = int(active_clock.now().timestamp() * 1000)

    with _lock:
        if timestamp_ms == _last_timestamp_ms:
            if _last_randomness >= _MAX_RANDOMNESS:
                # 极端情况：同毫秒耗尽 80 位随机空间，推进一毫秒重来。
                timestamp_ms += 1
                randomness = int.from_bytes(os.urandom(10), "big")
            else:
                randomness = _last_randomness + 1
        else:
            randomness = int.from_bytes(os.urandom(10), "big")
        _last_timestamp_ms = timestamp_ms
        _last_randomness = randomness

    return _encode(timestamp_ms, _ENCODE_LEN_TIME) + _encode(randomness, _ENCODE_LEN_RANDOM)


def new_trace_id(*, clock: Clock | None = None) -> str:
    """生成分布式追踪 ID。

    与 ULID 同构（26 位），这样日志、事件表、HTTP ``X-Trace-Id`` 三处格式统一。
    """
    return new_ulid(clock=clock)


def short_id(length: int = 8, *, clock: Clock | None = None) -> str:
    """生成短 ID —— 用于分支名、容器名等对长度敏感的场景。

    取新 ULID 的随机段尾部，冲突概率在单个 Run 内可忽略。

    :param length: 长度，取值范围 ``1..16``。
    :raises ValueError: 长度越界。
    """
    if not 1 <= length <= _ENCODE_LEN_RANDOM:
        raise ValueError(f"short_id length 必须在 1..{_ENCODE_LEN_RANDOM} 之间，收到 {length}")
    return new_ulid(clock=clock)[-length:].lower()


def deterministic_ulid(seed: str, *, timestamp_ms: int = 0) -> str:
    """由字符串种子确定性地派生 ULID。

    同样的 ``(seed, timestamp_ms)`` 永远产出同样的 ID，用于：

    * MockProvider 的可复现输出；
    * 单测里需要稳定 ID 的断言；
    * 幂等键派生的辅助场景。

    :param seed: 任意字符串种子。
    :param timestamp_ms: 时间段使用的毫秒时间戳，默认 0（使 ID 全确定）。
    """
    digest = sha256_hex(seed)
    randomness = int(digest[:20], 16) & _MAX_RANDOMNESS
    return _encode(timestamp_ms, _ENCODE_LEN_TIME) + _encode(randomness, _ENCODE_LEN_RANDOM)


def seeded_generator(seed: int) -> Iterator[str]:
    """返回一个由整数种子驱动的确定性 ULID 生成器。

    >>> gen = seeded_generator(42)
    >>> a, b = next(gen), next(gen)
    >>> a != b and len(a) == 26
    True
    >>> next(seeded_generator(42)) == a
    True
    """
    rng = random.Random(seed)  # noqa: S311 —— 仅用于测试确定性，不用于安全场景
    counter = 0
    while True:
        counter += 1
        randomness = rng.getrandbits(80)
        yield _encode(counter, _ENCODE_LEN_TIME) + _encode(randomness, _ENCODE_LEN_RANDOM)


def is_ulid(value: str) -> bool:
    """判断字符串是否为合法 ULID。"""
    if len(value) != ULID_LENGTH:
        return False
    return all(ch.upper() in _DECODE_MAP for ch in value)


def ulid_timestamp_ms(value: str) -> int:
    """从 ULID 中提取毫秒时间戳。

    :raises ValueError: 输入不是合法 ULID。
    """
    if not is_ulid(value):
        raise ValueError(f"不是合法的 ULID: {value!r}")
    return _decode(value[:_ENCODE_LEN_TIME])
