"""Token 计数 —— tiktoken 可选，缺失时自动降级为字符启发式。

为什么不硬依赖 tiktoken
-----------------------
1. tiktoken 需要下载编码文件，离线 / 内网环境直接挂；
2. Qwen / Claude / DeepSeek 的分词器与 GPT 并不一致，装了也不准；
3. 本项目对 token 数的用途是**预算预检**与**上下文裁剪**，
   ``±15%`` 的误差完全够用（DoD 要求：降级后误差 < 15%）。

因此策略是：**能用就用，用不了就估**，并且估算算法对中英文分别标定。

降级算法
--------
按字符分类累加，再加上每条消息的协议开销：

* CJK 表意文字：``1.00`` token / 字（GPT / Qwen 的中文均接近 1:1，Qwen 略优）
* 拉丁字母与空格：``0.25`` token / 字符（即经典的「4 字符 ≈ 1 token」）
* 数字：``0.33`` token / 字符
* 其余标点与符号：``0.50`` token / 字符

消息协议开销：每条 ``+4``，整轮 ``+3``；工具声明按其 JSON 文本估算并 ``+8``。
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Final

from loopforge.providers.types import Message, ToolSpec

__all__ = [
    "TIKTOKEN_AVAILABLE",
    "count_text",
    "count_messages",
    "count_tools",
    "count_tool_calls",
    "estimate_chars_per_token",
    "encoding_name_for",
    "clear_encoding_cache",
]

try:  # pragma: no cover —— 取决于是否装了可选依赖
    import tiktoken as _tiktoken

    TIKTOKEN_AVAILABLE: Final[bool] = True
except ImportError:  # pragma: no cover
    _tiktoken = None  # type: ignore[assignment]
    TIKTOKEN_AVAILABLE = False


#: 模型前缀 → tiktoken 编码名。按前缀最长匹配。
_ENCODING_BY_PREFIX: Final[tuple[tuple[str, str], ...]] = (
    ("gpt-4o", "o200k_base"),
    ("gpt-4.1", "o200k_base"),
    ("gpt-5", "o200k_base"),
    ("o1", "o200k_base"),
    ("o3", "o200k_base"),
    ("o4", "o200k_base"),
    ("gpt-4", "cl100k_base"),
    ("gpt-3.5", "cl100k_base"),
    ("text-embedding-3", "cl100k_base"),
    ("text-embedding-ada", "cl100k_base"),
)

#: 非 OpenAI 家族统一回落到该编码做近似（比纯字符估算准）。
_FALLBACK_ENCODING: Final[str] = "cl100k_base"

#: 每条消息的协议开销 token。
_PER_MESSAGE_OVERHEAD: Final[int] = 4

#: 整轮对话的固定开销 token（assistant priming）。
_PER_REQUEST_OVERHEAD: Final[int] = 3

#: 每个工具声明的额外开销 token。
_PER_TOOL_OVERHEAD: Final[int] = 8

#: 各字符类别的 token 权重。
_W_CJK: Final[float] = 1.00
_W_LATIN: Final[float] = 0.25
_W_DIGIT: Final[float] = 0.33
_W_OTHER: Final[float] = 0.50

#: CJK 相关 Unicode 区段（含中日韩表意文字、假名、谚文与全角标点）。
_CJK_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (0x1100, 0x11FF),  # 谚文字母
    (0x2E80, 0x2FFF),  # 康熙部首
    (0x3000, 0x303F),  # CJK 标点
    (0x3040, 0x30FF),  # 平假名 / 片假名
    (0x3130, 0x318F),  # 谚文兼容字母
    (0x3400, 0x4DBF),  # CJK 扩展 A
    (0x4E00, 0x9FFF),  # CJK 基本区
    (0xA960, 0xA97F),  # 谚文扩展 A
    (0xAC00, 0xD7AF),  # 谚文音节
    (0xF900, 0xFAFF),  # CJK 兼容表意
    (0xFF00, 0xFFEF),  # 全角字符
    (0x20000, 0x2A6DF),  # CJK 扩展 B
)


def _is_cjk(char: str) -> bool:
    """判断字符是否属于 CJK 区段。

    Args:
        char: 单个字符。

    Returns:
        属于 CJK 返回 ``True``。
    """
    point = ord(char)
    return any(low <= point <= high for low, high in _CJK_RANGES)


def encoding_name_for(model: str) -> str:
    """把模型名映射到 tiktoken 编码名。

    Args:
        model: 模型名。

    Returns:
        编码名；未知模型返回 :data:`_FALLBACK_ENCODING`。
    """
    key = (model or "").strip().lower()
    best_name = _FALLBACK_ENCODING
    best_len = -1
    for prefix, encoding in _ENCODING_BY_PREFIX:
        if key.startswith(prefix) and len(prefix) > best_len:
            best_name, best_len = encoding, len(prefix)
    return best_name


@lru_cache(maxsize=16)
def _get_encoding(name: str) -> Any:
    """按名字取 tiktoken 编码器（带缓存）。

    Args:
        name: 编码名。

    Returns:
        编码器对象；tiktoken 不可用或加载失败时返回 ``None``。
    """
    if not TIKTOKEN_AVAILABLE or _tiktoken is None:
        return None
    try:  # pragma: no cover —— 依赖外部编码文件
        return _tiktoken.get_encoding(name)
    except Exception:  # noqa: BLE001 —— 下载失败 / 文件损坏都降级
        return None


def clear_encoding_cache() -> None:
    """清空编码器缓存（测试在切换 tiktoken 可用性时调用）。"""
    _get_encoding.cache_clear()


def _heuristic_count(text: str) -> int:
    """字符启发式估算 token 数。

    Args:
        text: 待估算文本。

    Returns:
        token 估算值，空文本返回 0。
    """
    if not text:
        return 0
    score = 0.0
    for char in text:
        if _is_cjk(char):
            score += _W_CJK
        elif char.isascii() and (char.isalpha() or char.isspace()):
            score += _W_LATIN
        elif char.isdigit():
            score += _W_DIGIT
        elif unicodedata.category(char).startswith("L"):
            score += _W_LATIN
        else:
            score += _W_OTHER
    return max(1, int(score + 0.5))


def count_text(text: str, model: str = "") -> int:
    """估算一段文本的 token 数。

    Args:
        text: 文本。
        model: 模型名，用于挑选 tiktoken 编码。

    Returns:
        token 数；tiktoken 可用时为精确值，否则为启发式估算。
    """
    if not text:
        return 0
    encoder = _get_encoding(encoding_name_for(model))
    if encoder is not None:  # pragma: no cover —— 取决于可选依赖
        try:
            return len(encoder.encode(text, disallowed_special=()))
        except Exception:  # noqa: BLE001 —— 编码异常也降级
            return _heuristic_count(text)
    return _heuristic_count(text)


def count_tool_calls(message: Message, model: str = "") -> int:
    """估算一条助手消息里工具调用部分的 token 数。

    Args:
        message: 消息。
        model: 模型名。

    Returns:
        token 数。
    """
    if not message.tool_calls:
        return 0
    total = 0
    for call in message.tool_calls:
        total += count_text(call.name, model)
        total += count_text(call.arguments_json(), model)
        total += 4  # 每个 tool_call 的结构开销
    return total


def count_tools(tools: Sequence[ToolSpec], model: str = "") -> int:
    """估算工具声明占用的 token 数。

    Args:
        tools: 工具声明序列。
        model: 模型名。

    Returns:
        token 数；无工具返回 0。
    """
    if not tools:
        return 0
    total = 0
    for spec in tools:
        payload = json.dumps(
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        total += count_text(payload, model) + _PER_TOOL_OVERHEAD
    return total


def count_messages(
    messages: Sequence[Message],
    model: str = "",
    *,
    tools: Sequence[ToolSpec] = (),
) -> int:
    """估算整轮对话的 prompt token 数。

    Args:
        messages: 消息序列。
        model: 模型名。
        tools: 随请求发送的工具声明。

    Returns:
        token 估算值（含协议开销）。
    """
    total = _PER_REQUEST_OVERHEAD if messages else 0
    for message in messages:
        total += _PER_MESSAGE_OVERHEAD
        total += count_text(message.text(), model)
        total += count_tool_calls(message, model)
        if message.name:
            total += count_text(message.name, model)
        if message.tool_call_id:
            total += 2
    total += count_tools(tools, model)
    return total


def estimate_chars_per_token(text: str, model: str = "") -> float:
    """返回文本的「字符/token」比，供文档与调参参考。

    Args:
        text: 文本。
        model: 模型名。

    Returns:
        比值；空文本返回 ``0.0``。
    """
    tokens = count_text(text, model)
    return len(text) / tokens if tokens else 0.0
