"""Ollama 本地推理 Provider —— NDJSON 协议，零成本计量。

Ollama 原生端点 ``POST /api/chat`` 采用 NDJSON：非流式时返回单行 JSON
（``done: true``），流式时每行一个增量（``message.content`` 为**整段累积**或
增量，视版本而定；本实现按「整段覆盖 + done 标记」兼容两种形态）。

与云端 Provider 的差异
----------------------
* **无鉴权头**（本地服务）；
* **零成本**（``free = True``，价目表里 ollama 全为 0）；
* 请求体直接用模型名，无需 ``base_url`` 之外的路径拼接；
* 工具调用格式：``message.tool_calls = [{"function": {"name", "arguments"}}]``，
  其中 ``arguments`` 是**对象**。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, ClassVar, Final

import httpx

from loopforge.core.logging import get_logger
from loopforge.providers.base import HttpProviderBase, ProviderBadResponseError
from loopforge.providers.streaming import aiter_ndjson, iter_dicts
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
    "OllamaProvider",
    "DEFAULT_OLLAMA_BASE_URL",
    "build_ollama_body",
    "parse_ollama_payload",
]

_LOG = get_logger(__name__)

#: Ollama 默认端点。
DEFAULT_OLLAMA_BASE_URL: Final[str] = "http://localhost:11434"

#: 默认上下文窗口。
_CONTEXT_WINDOW: Final[int] = 131_072
_MAX_OUTPUT: Final[int] = 8_192


def build_ollama_body(request: ChatRequest, *, stream: bool = False) -> dict[str, Any]:
    """构造 Ollama ``/api/chat`` 请求体。

    Args:
        request: 对话请求。
        stream: 是否流式。

    Returns:
        请求体字典。
    """
    messages: list[dict[str, Any]] = []
    for message in request.messages:
        item: dict[str, Any] = {"role": message.role}
        if message.content is not None:
            item["content"] = message.content
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments or {},
                    }
                }
                for call in message.tool_calls
            ]
        messages.append(item)

    body: dict[str, Any] = {
        "model": request.model,
        "messages": messages,
        "stream": stream,
        "options": {"temperature": request.temperature},
    }
    if request.max_tokens is not None:
        body["options"]["num_predict"] = request.max_tokens
    if request.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in request.tools
        ]
    if request.stop:
        body["options"]["stop"] = list(request.stop)
    if request.seed is not None:
        body["options"]["seed"] = request.seed
    return body


def parse_ollama_usage(payload: Mapping[str, Any]) -> Usage:
    """从 Ollama 响应解析用量。"""
    return Usage(
        prompt_tokens=int(payload.get("prompt_eval_count", 0) or 0),
        completion_tokens=int(payload.get("eval_count", 0) or 0),
        cached_tokens=0,
    )


def parse_ollama_payload(
    payload: Mapping[str, Any], *, provider_name: str, model: str
) -> ChatResult:
    """把 Ollama 非流式响应解析成 :class:`ChatResult`。

    Args:
        payload: 响应体字典。
        provider_name: Provider 注册名。
        model: 模型名。

    Returns:
        解析结果。

    Raises:
        ProviderBadResponseError: 响应缺少 ``message``。
    """
    message = payload.get("message")
    if not isinstance(message, Mapping):
        raise ProviderBadResponseError(
            f"{provider_name} 响应缺少 message",
            details={"provider": provider_name, "model": model, "payload_keys": list(payload)},
        )
    content = message.get("content")
    tool_calls: list[ToolCallRequest] = []
    for index, item in enumerate(iter_dicts(message.get("tool_calls"))):
        function = item.get("function")
        if not isinstance(function, Mapping):
            continue
        name = str(function.get("name", "") or "")
        if not name:
            continue
        arguments_raw = function.get("arguments")
        if isinstance(arguments_raw, Mapping):
            arguments = dict(arguments_raw)
            raw_text = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        else:
            arguments = {}
            raw_text = str(arguments_raw or "")
        tool_calls.append(
            ToolCallRequest(
                id=f"ollama_{index}",
                name=name,
                arguments=arguments,
                raw_arguments=raw_text,
            )
        )
    return ChatResult(
        content=content or "",
        tool_calls=tool_calls,
        usage=parse_ollama_usage(payload),
        model=str(payload.get("model", model) or model),
        finish_reason="tool_calls" if tool_calls else "stop",
        raw=dict(payload),
    )


class OllamaProvider(HttpProviderBase):
    """Ollama 本地推理 Provider。"""

    name: ClassVar[str] = "ollama"
    aliases: ClassVar[tuple[str, ...]] = ("ollama-local", "local")
    kind: ClassVar[str] = "ollama"
    default_base_url: ClassVar[str] = DEFAULT_OLLAMA_BASE_URL
    default_model: ClassVar[str] = "qwen3.8-27b"
    free: ClassVar[bool] = True

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Ollama 支持工具 / 流式 / seed，JSON 模式视模型而定。"""
        return ProviderCapabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_json_mode=True,
            supports_seed=True,
            context_window=_CONTEXT_WINDOW,
            max_output_tokens=_MAX_OUTPUT,
            supports_embedding=False,
        )

    async def chat(self, request: ChatRequest) -> ChatResult:
        """非流式对话补全。"""
        started = self.clock.monotonic()
        model = self.resolve_model(request.model)
        body = build_ollama_body(request)
        body["model"] = model
        payload = await self.request_json(
            "POST",
            "/api/chat",
            json_body=body,
            model=model,
            timeout_s=request.timeout_s,
        )
        result = parse_ollama_payload(payload, provider_name=self.name, model=model)
        result.latency_ms = self.elapsed_ms(started)
        return result

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        """流式对话补全（NDJSON）。"""
        started = self.clock.monotonic()
        model = self.resolve_model(request.model)
        body = build_ollama_body(request, stream=True)
        body["model"] = model
        client = self.client()
        try:
            async with client.stream(
                "POST",
                "/api/chat",
                json=body,
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
                usage = Usage()
                async for item in aiter_ndjson(response):
                    message = item.get("message")
                    if isinstance(message, Mapping):
                        content = message.get("content")
                        if isinstance(content, str) and content:
                            yield StreamChunk(delta=content, index=0, raw=item)
                        for index, tc in enumerate(iter_dicts(message.get("tool_calls"))):
                            function = tc.get("function")
                            if isinstance(function, Mapping):
                                arguments_raw = function.get("arguments")
                                arguments = (
                                    dict(arguments_raw)
                                    if isinstance(arguments_raw, Mapping)
                                    else {}
                                )
                                yield StreamChunk(
                                    tool_call_delta=ToolCallRequest(
                                        id=f"ollama_{index}",
                                        name=str(function.get("name", "") or ""),
                                        arguments=arguments,
                                        raw_arguments=json.dumps(
                                            arguments, ensure_ascii=False, sort_keys=True
                                        ),
                                    ),
                                    index=0,
                                    raw=item,
                                )
                    if item.get("done"):
                        usage = parse_ollama_usage(item)
                        yield StreamChunk(
                            finish_reason="stop",
                            usage=usage,
                            index=0,
                            raw=item,
                        )
                        return
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
        """探活：GET ``/api/tags``。"""
        return await self.probe("/api/tags", expect=200)
