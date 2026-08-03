"""Anthropic Messages API Provider —— Claude 系模型。

协议要点（``docs/ARCHITECTURE.md`` §4.2）
----------------------------------------
* 端点：``POST https://api.anthropic.com/v1/messages``；
* 鉴权：``x-api-key`` 头 + ``anthropic-version`` 头；
* 请求体：``{"model", "messages", "max_tokens", "system", "tools", "temperature"}``；
* 角色只认 ``user`` / ``assistant``（system 单独提出来放 ``system`` 字段）；
* 响应：``content`` 是**对象数组**，``{"type": "text", "text": ...}`` 与
  ``{"type": "tool_use", "id", "name", "input"}`` 混排；
* 流式：SSE 带 ``event:`` 名（``content_block_delta`` / ``message_delta`` 等），
  文本增量在 ``delta.text``，工具参数增量在 ``delta.input_json_delta.partial_json``。

本模块统一把 Anthropic 方言收敛到内部 :class:`ChatResult` / :class:`StreamChunk`。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, ClassVar, Final

import httpx

from loopforge.core.logging import get_logger
from loopforge.providers.base import HttpProviderBase, ProviderBadResponseError
from loopforge.providers.streaming import aiter_sse, iter_dicts
from loopforge.providers.token_counter import count_messages, count_text
from loopforge.providers.types import (
    ChatRequest,
    ChatResult,
    Message,
    ProviderCapabilities,
    ProviderConfigModel,
    ProviderHealth,
    StreamChunk,
    ToolCallRequest,
    ToolSpec,
    Usage,
)

__all__ = [
    "AnthropicProvider",
    "DEFAULT_ANTHROPIC_BASE_URL",
    "ANTHROPIC_VERSION",
    "build_anthropic_messages",
    "build_anthropic_tools",
    "parse_anthropic_payload",
]

_LOG = get_logger(__name__)

#: Anthropic 默认端点。
DEFAULT_ANTHROPIC_BASE_URL: Final[str] = "https://api.anthropic.com"

#: 当前支持的 API 版本头。
ANTHROPIC_VERSION: Final[str] = "2023-06-01"

#: 默认上下文窗口。
_CONTEXT_WINDOW: Final[int] = 200_000
_MAX_OUTPUT: Final[int] = 64_000


def build_anthropic_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """把内部消息转换成 Anthropic ``messages`` 数组。

    system 消息会被过滤（由调用方提取到 ``system`` 字段）。

    Args:
        messages: 内部消息序列。

    Returns:
        Anthropic 格式消息列表。
    """
    result: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        item: dict[str, Any] = {"role": message.role}
        if message.tool_calls:
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id or f"toolu_{abs(hash(call.name)):012d}",
                        "name": call.name,
                        "input": call.arguments or {},
                    }
                )
            item["content"] = blocks
        elif message.role == "tool":
            item["content"] = [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id or "",
                    "content": message.text(),
                }
            ]
        else:
            item["content"] = message.text()
        result.append(item)
    return result


def build_anthropic_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """把内部工具声明转换成 Anthropic ``tools`` 数组。

    Args:
        tools: 内部工具声明。

    Returns:
        Anthropic 格式工具列表。
    """
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.parameters,
        }
        for spec in tools
    ]


def parse_anthropic_usage(payload: Mapping[str, Any]) -> Usage:
    """从 Anthropic 响应体解析用量。"""
    raw = payload.get("usage")
    if not isinstance(raw, Mapping):
        return Usage()
    return Usage(
        prompt_tokens=int(raw.get("input_tokens", 0) or 0),
        completion_tokens=int(raw.get("output_tokens", 0) or 0),
        cached_tokens=int(
            raw.get("cache_read_input_tokens", 0) or 0
        ),
    )


def _normalize_anthropic_stop(stop: Any) -> str:
    """把 Anthropic stop_reason 归一成内部五元组。"""
    mapping: dict[str, str] = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
    }
    return mapping.get(str(stop or ""), "stop")


def parse_anthropic_payload(
    payload: Mapping[str, Any], *, provider_name: str, model: str
) -> ChatResult:
    """把 Anthropic 非流式响应解析成 :class:`ChatResult`。

    Args:
        payload: 响应体字典。
        provider_name: Provider 注册名。
        model: 模型名。

    Returns:
        解析结果。

    Raises:
        ProviderBadResponseError: 响应缺少 ``content``。
    """
    content_blocks = payload.get("content")
    if not isinstance(content_blocks, list):
        raise ProviderBadResponseError(
            f"{provider_name} 响应缺少 content",
            details={"provider": provider_name, "model": model, "payload_keys": list(payload)},
        )
    text_parts: list[str] = []
    tool_calls: list[ToolCallRequest] = []
    for index, block in enumerate(iter_dicts(content_blocks)):
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(str(block.get("text", "") or ""))
        elif block_type == "tool_use":
            tool_calls.append(
                ToolCallRequest(
                    id=str(block.get("id", "") or f"toolu_{index}"),
                    name=str(block.get("name", "") or ""),
                    arguments=dict(block.get("input") or {}),
                    raw_arguments=__import__("json").dumps(
                        block.get("input") or {}, ensure_ascii=False, sort_keys=True
                    ),
                )
            )
    return ChatResult(
        content="".join(text_parts),
        tool_calls=tool_calls,
        usage=parse_anthropic_usage(payload),
        model=str(payload.get("model", model) or model),
        finish_reason=_normalize_anthropic_stop(payload.get("stop_reason")),  # type: ignore[arg-type]
        raw=dict(payload),
    )


class AnthropicProvider(HttpProviderBase):
    """Anthropic Messages API Provider（Claude 系）。"""

    name: ClassVar[str] = "anthropic"
    aliases: ClassVar[tuple[str, ...]] = ("claude", "anthropic-messages")
    kind: ClassVar[str] = "anthropic"
    default_base_url: ClassVar[str] = DEFAULT_ANTHROPIC_BASE_URL
    default_model: ClassVar[str] = "claude-sonnet-4-5"
    free: ClassVar[bool] = False

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Claude 支持工具 / 流式 / JSON / seed。"""
        return ProviderCapabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_json_mode=True,
            supports_seed=False,
            context_window=_CONTEXT_WINDOW,
            max_output_tokens=_MAX_OUTPUT,
            supports_embedding=False,
        )

    def default_headers(self) -> dict[str, str]:
        """追加 ``x-api-key`` 与 ``anthropic-version`` 头。"""
        headers = super().default_headers()
        key = self.config.secret()
        if key:
            headers["x-api-key"] = key
        headers["anthropic-version"] = ANTHROPIC_VERSION
        return headers

    def _build_body(self, request: ChatRequest, *, stream: bool = False) -> dict[str, Any]:
        """构造 Anthropic ``/v1/messages`` 请求体。"""
        body: dict[str, Any] = {
            "model": self.resolve_model(request.model),
            "messages": build_anthropic_messages(request.messages),
            "max_tokens": request.max_tokens or self.capabilities.max_output_tokens,
            "temperature": request.temperature,
            "stream": stream,
        }
        system_text = "\n\n".join(
            m.text() for m in request.messages if m.role == "system" and m.text()
        )
        if system_text:
            body["system"] = system_text
        if request.tools:
            body["tools"] = build_anthropic_tools(request.tools)
        if request.stop:
            body["stop_sequences"] = list(request.stop)
        return body

    async def chat(self, request: ChatRequest) -> ChatResult:
        """非流式对话补全。"""
        started = self.clock.monotonic()
        model = self.resolve_model(request.model)
        payload = await self.request_json(
            "POST",
            "/v1/messages",
            json_body=self._build_body(request),
            model=model,
            timeout_s=request.timeout_s,
        )
        result = parse_anthropic_payload(payload, provider_name=self.name, model=model)
        result.latency_ms = self.elapsed_ms(started)
        return result

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        """流式对话补全（Anthropic SSE）。"""
        started = self.clock.monotonic()
        model = self.resolve_model(request.model)
        client = self.client()
        try:
            async with client.stream(
                "POST",
                "/v1/messages",
                json=self._build_body(request, stream=True),
                headers=self.default_headers(),
                timeout=request.timeout_s,
            ) as response:
                if response.status_code >= 400:
                    text = (await response.aread()).decode("utf-8", errors="replace")
                    from loopforge.providers.base import map_http_status

                    raise map_http_status(
                        response.status_code,
                        provider=self.name,
                        model=model,
                        body=text,
                    )
                tool_parts: dict[int, dict[str, Any]] = {}
                usage = Usage()
                async for event in aiter_sse(response):
                    if event.is_done:
                        break
                    data = event.json()
                    if not data:
                        continue
                    event_name = event.event
                    if event_name == "message_start":
                        usage = parse_anthropic_usage(data.get("message") or {})
                        continue
                    if event_name == "message_delta":
                        usage = parse_anthropic_usage(data.get("usage") or {})
                        stop = data.get("delta", {}).get("stop_reason")
                        if stop is not None:
                            yield StreamChunk(
                                finish_reason=_normalize_anthropic_stop(stop),  # type: ignore[arg-type]
                                usage=usage,
                                index=0,
                                raw=data,
                            )
                            return
                        continue
                    if event_name == "content_block_delta":
                        delta = data.get("delta")
                        if not isinstance(delta, Mapping):
                            continue
                        text = delta.get("text")
                        if isinstance(text, str) and text:
                            yield StreamChunk(delta=text, index=0, raw=data)
                            continue
                        partial = (
                            delta.get("input_json_delta", {}).get("partial_json")
                            if isinstance(delta.get("input_json_delta"), Mapping)
                            else None
                        )
                        if isinstance(partial, str) and partial:
                            index = int(data.get("index", 0) or 0)
                            part = tool_parts.setdefault(
                                index, {"id": "", "name": "", "json": ""}
                            )
                            part["json"] += partial
                            completed = _try_complete_tool(part, index)
                            if completed is not None:
                                yield StreamChunk(
                                    tool_call_delta=completed, index=0, raw=data
                                )
                        continue
                    if event_name == "content_block_start":
                        block = data.get("content_block")
                        if isinstance(block, Mapping) and block.get("type") == "tool_use":
                            index = int(data.get("index", 0) or 0)
                            part = tool_parts.setdefault(
                                index, {"id": "", "name": "", "json": ""}
                            )
                            part["id"] = str(block.get("id", "") or "")
                            part["name"] = str(block.get("name", "") or "")
                # 收尾：把未闭合的工具参数尽力解析。
                for index in sorted(tool_parts):
                    completed = _try_complete_tool(tool_parts[index], index)
                    if completed is not None:
                        yield StreamChunk(
                            tool_call_delta=completed, index=0, raw={"stream_end": True}
                        )
                yield StreamChunk(
                    finish_reason="stop",
                    usage=usage,
                    index=0,
                    raw={"stream_end": True},
                )
        except httpx.TimeoutException as exc:
            from loopforge.providers.base import map_transport_error

            raise map_transport_error(
                exc, provider=self.name, model=model, timeout_s=request.timeout_s
            ) from exc
        except httpx.TransportError as exc:
            from loopforge.providers.base import map_transport_error

            raise map_transport_error(
                exc, provider=self.name, model=model, timeout_s=request.timeout_s
            ) from exc

    def count_tokens(self, messages: Sequence[Message], model: str) -> int:
        """估算 prompt token 数。"""
        return count_messages(messages, model)

    async def health_check(self) -> ProviderHealth:
        """探活：Anthropic 无免费 list 端点，发一个空 messages 请求。"""
        started = self.clock.monotonic()
        try:
            payload = await self.request_json(
                "POST",
                "/v1/messages",
                json_body={
                    "model": self.resolve_model(None),
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                },
                model=self.resolve_model(None),
                timeout_s=10.0,
            )
        except Exception as exc:  # noqa: BLE001 —— health_check 契约要求不抛
            from loopforge.providers.base import map_transport_error

            mapped = map_transport_error(exc, provider=self.name)
            return ProviderHealth(
                name=self.name,
                healthy=False,
                latency_ms=self.elapsed_ms(started),
                checked_at=self.clock.now(),
                detail=str(exc),
                model=self.config.default_model,
                error_code=mapped.code,
            )
        return ProviderHealth(
            name=self.name,
            healthy=True,
            latency_ms=self.elapsed_ms(started),
            checked_at=self.clock.now(),
            detail="messages ping ok",
            model=self.config.default_model,
        )


def _try_complete_tool(part: dict[str, Any], index: int) -> ToolCallRequest | None:
    """尝试把累积的工具参数 JSON 解析成完整调用。

    Args:
        part: 累积状态（``id`` / ``name`` / ``json``）。
        index: 块索引。

    Returns:
        解析成功返回 :class:`ToolCallRequest`，否则 ``None``。
    """
    if not part["name"] or not part["json"]:
        return None
    text = part["json"].strip()
    import json

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return ToolCallRequest(
        id=part["id"] or f"toolu_{index}",
        name=part["name"],
        arguments=parsed,
        raw_arguments=text,
    )
