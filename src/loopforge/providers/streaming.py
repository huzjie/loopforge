"""流式响应解析 —— SSE / NDJSON 与工具调用增量聚合。

三家方言的流式格式差异
----------------------
======================  ==========================================================
Provider                 格式
======================  ==========================================================
OpenAI 兼容 / DashScope   ``text/event-stream``，每块 ``data: {json}``，以 ``data: [DONE]`` 收尾
Anthropic                ``text/event-stream``，带 ``event:`` 名（``content_block_delta`` 等）
Ollama                   NDJSON，一行一个 JSON 对象，最后一行 ``done: true``
======================  ==========================================================

本模块把三者统一成 :class:`~loopforge.providers.types.StreamChunk` 序列。

粘包与半包
----------
SSE 的一个「事件块」以空行分隔，一行 ``data:`` 可能被 TCP 切断在任意位置。
:func:`aiter_sse` 基于 ``httpx`` 的 ``aiter_lines()``，由 httpx 保证按行切分，
本模块只负责把「多行 → 一个事件」的状态机做对。

工具调用增量
------------
OpenAI 流式把一个 tool_call 拆成若干片段（``index`` 相同、``arguments`` 逐字符追加）。
:class:`ToolCallAccumulator` 负责按 ``index`` 归并，**只在参数 JSON 完整后**
才吐出 :class:`ToolCallRequest`，上层因此永远看不到半截 JSON。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field, field
from typing import Any, Final

import httpx

from loopforge.providers.types import (
    ChatResult,
    FinishReason,
    StreamChunk,
    ToolCallRequest,
    Usage,
)

__all__ = [
    "SSEEvent",
    "SSE_DONE",
    "parse_sse_block",
    "aiter_sse",
    "aiter_sse_from_lines",
    "aiter_ndjson",
    "ToolCallAccumulator",
    "collect_stream",
    "chunk_text",
    "iter_dicts",
]

#: OpenAI 家族的流结束哨兵。
SSE_DONE: Final[str] = "[DONE]"


@dataclass(slots=True)
class SSEEvent:
    """一个 Server-Sent Event 事件块。

    Attributes:
        event: ``event:`` 字段，缺省 ``"message"``。
        data: ``data:`` 字段（多行会用 ``\\n`` 连接）。
        id: ``id:`` 字段。
        retry: ``retry:`` 字段（毫秒）。
    """

    event: str = "message"
    data: str = ""
    id: str = ""
    retry: int | None = None

    @property
    def is_done(self) -> bool:
        """是否是 OpenAI 家族的 ``[DONE]`` 哨兵。"""
        return self.data.strip() == SSE_DONE

    def json(self) -> dict[str, Any]:
        """把 ``data`` 解析成 JSON 对象。

        Returns:
            解析结果；不是合法 JSON 对象时返回空字典。
        """
        text = self.data.strip()
        if not text or text == SSE_DONE:
            return {}
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}


def parse_sse_block(block: str) -> SSEEvent:
    """把一个「以空行分隔的事件块」解析成 :class:`SSEEvent`。

    Args:
        block: 事件块原文（不含末尾空行）。

    Returns:
        解析后的事件。
    """
    event = SSEEvent()
    data_lines: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.rstrip("\r")
        if not line or line.startswith(":"):
            continue
        if ":" in line:
            field_name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
        else:
            field_name, value = line, ""
        key = field_name.strip()
        if key == "data":
            data_lines.append(value)
        elif key == "event":
            event.event = value.strip() or "message"
        elif key == "id":
            event.id = value.strip()
        elif key == "retry":
            try:
                event.retry = int(value.strip())
            except ValueError:
                event.retry = None
    event.data = "\n".join(data_lines)
    return event


async def aiter_sse_from_lines(lines: AsyncIterator[str]) -> AsyncIterator[SSEEvent]:
    """从「一行一行」的异步迭代器里还原 SSE 事件流。

    Args:
        lines: 逐行的异步迭代器（httpx 的 ``aiter_lines()``）。

    Yields:
        :class:`SSEEvent`，遇到 ``[DONE]`` 后停止。
    """
    buffer: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\n").rstrip("\r")
        if line:
            buffer.append(line)
            continue
        if not buffer:
            continue
        event = parse_sse_block("\n".join(buffer))
        buffer.clear()
        if event.is_done:
            return
        if event.data or event.event != "message":
            yield event
    if buffer:
        event = parse_sse_block("\n".join(buffer))
        if not event.is_done and (event.data or event.event != "message"):
            yield event


async def aiter_sse(response: httpx.Response) -> AsyncIterator[SSEEvent]:
    """从 ``httpx`` 流式响应里读取 SSE 事件。

    Args:
        response: 已进入流模式的响应对象。

    Yields:
        :class:`SSEEvent`。
    """
    async for event in aiter_sse_from_lines(response.aiter_lines()):
        yield event


async def aiter_ndjson(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """从 ``httpx`` 流式响应里读取 NDJSON（Ollama 用）。

    Args:
        response: 已进入流模式的响应对象。

    Yields:
        每行解析出的 JSON 对象；非法行直接跳过。
    """
    async for raw in response.aiter_lines():
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict):
            yield payload


@dataclass(slots=True)
class _PartialToolCall:
    """累积中的工具调用碎片。"""

    id: str = ""
    name: str = ""
    arguments: str = ""
    emitted: bool = False


class ToolCallAccumulator:
    """OpenAI 流式 ``tool_calls`` 增量聚合器。

    用法::

        acc = ToolCallAccumulator()
        for delta in deltas:
            acc.feed(delta)          # delta 是 choices[0].delta.tool_calls 的一项
        calls = acc.finish()          # 完整的 ToolCallRequest 列表

    ``feed`` 返回**刚刚变完整**的调用（参数 JSON 可解析且非空），
    这样流式消费者可以在参数闭合的瞬间就拿到结构化结果。
    """

    def __init__(self) -> None:
        """初始化空累积器。"""
        self._parts: dict[int, _PartialToolCall] = {}
        self._order: list[int] = []

    def feed(self, delta: Mapping[str, Any]) -> ToolCallRequest | None:
        """喂入一个 ``tool_calls`` 增量项。

        Args:
            delta: 形如 ``{"index": 0, "id": "...", "function": {"name": ..., "arguments": ...}}``。

        Returns:
            若本次喂入让某个调用的参数变完整，返回该调用；否则 ``None``。
        """
        try:
            index = int(delta.get("index", 0) or 0)
        except (TypeError, ValueError):
            index = 0
        part = self._parts.get(index)
        if part is None:
            part = _PartialToolCall()
            self._parts[index] = part
            self._order.append(index)

        call_id = delta.get("id")
        if isinstance(call_id, str) and call_id:
            part.id = call_id
        function = delta.get("function")
        if isinstance(function, Mapping):
            name = function.get("name")
            if isinstance(name, str) and name:
                part.name = name
            args = function.get("arguments")
            if isinstance(args, str) and args:
                part.arguments += args

        if part.emitted or not part.name:
            return None
        text = part.arguments.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        part.emitted = True
        return ToolCallRequest(
            id=part.id or f"call_{index}",
            name=part.name,
            arguments=parsed,
            raw_arguments=text,
        )

    def finish(self) -> list[ToolCallRequest]:
        """收尾：返回全部工具调用（含参数未闭合的，尽力解析）。

        Returns:
            按 ``index`` 顺序排列的 :class:`ToolCallRequest` 列表。
        """
        results: list[ToolCallRequest] = []
        for index in self._order:
            part = self._parts[index]
            if not part.name:
                continue
            results.append(
                ToolCallRequest.from_json_text(
                    id=part.id or f"call_{index}",
                    name=part.name,
                    raw=part.arguments,
                )
            )
        return results

    def pending(self) -> int:
        """尚未吐出的碎片数量（调试用）。"""
        return sum(1 for part in self._parts.values() if not part.emitted)

    def reset(self) -> None:
        """清空累积状态。"""
        self._parts.clear()
        self._order.clear()


async def collect_stream(
    chunks: AsyncIterator[StreamChunk], *, model: str = "", latency_ms: int = 0
) -> ChatResult:
    """把流式片段合并成一个 :class:`ChatResult`。

    用途有二：
    1. 让「只实现了流式」的 Provider 免费获得非流式能力；
    2. 测试里对流式与非流式做等价性断言。

    Args:
        chunks: :class:`StreamChunk` 异步迭代器。
        model: 模型名（写入结果）。
        latency_ms: 端到端耗时（写入结果）。

    Returns:
        合并后的完整结果。
    """
    parts: list[str] = []
    tool_calls: list[ToolCallRequest] = []
    seen_ids: set[str] = set()
    usage = Usage()
    finish: FinishReason = "stop"
    raw: dict[str, Any] = {}

    async for chunk in chunks:
        if chunk.delta:
            parts.append(chunk.delta)
        if chunk.tool_call_delta is not None:
            key = chunk.tool_call_delta.id or f"{chunk.tool_call_delta.name}:{len(tool_calls)}"
            if key not in seen_ids:
                seen_ids.add(key)
                tool_calls.append(chunk.tool_call_delta)
        if chunk.usage is not None:
            usage = chunk.usage
        if chunk.finish_reason is not None:
            finish = chunk.finish_reason
        if chunk.raw:
            raw = chunk.raw

    return ChatResult(
        content="".join(parts),
        tool_calls=tool_calls,
        usage=usage,
        model=model,
        finish_reason=finish,
        latency_ms=latency_ms,
        raw=raw,
    )


def chunk_text(text: str, size: int) -> list[str]:
    """把文本按固定长度切片（MockProvider 造流式用，**确定性**）。

    Args:
        text: 原文。
        size: 每片字符数，必须为正。

    Returns:
        切片列表；空文本返回空列表。

    Raises:
        ValueError: ``size`` 非正。
    """
    if size <= 0:
        raise ValueError(f"chunk_text 的 size 必须为正，收到 {size}")
    if not text:
        return []
    return [text[start : start + size] for start in range(0, len(text), size)]


def iter_dicts(items: Iterable[Any]) -> list[dict[str, Any]]:
    """从任意可迭代对象中筛出字典元素（解析各家 ``choices`` 数组用）。

    Args:
        items: 可迭代对象；``None`` 或非迭代类型返回空列表。

    Returns:
        字典列表。
    """
    if not isinstance(items, Iterable) or isinstance(items, (str, bytes, Mapping)):
        return []
    return [item for item in items if isinstance(item, dict)]


_ = field  # 保持 dataclasses.field 导入被使用（_PartialToolCall 未用默认工厂）
