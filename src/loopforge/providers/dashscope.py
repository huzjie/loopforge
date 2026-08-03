"""阿里云百炼 DashScope Provider —— 通义千问 Qwen 系（含 qwen3.8-max / qwen3.8-27b）。

接入方式
--------
DashScope 提供两条通道（``ARCHITECTURE`` §4.2）：

1. **OpenAI 兼容通道**（推荐，``base_url`` 指向
   ``https://dashscope.aliyuncs.com/compatible-mode/v1``）：与
   :class:`~loopforge.providers.openai_compat.OpenAICompatProvider` 完全同构，
   只是默认端点不同；
2. **原生 DashScope 通道**（``base_url`` 指向
   ``https://dashscope.aliyuncs.com/api/v1``）：本类实现。

本类默认走**原生通道**，以兼容 qwen3.8 系列的完整参数与流式协议；需要兼容通道时
直接把 ``base_url`` 换成 compatible-mode 端点即可（无需改代码）。

原生通道要点
------------
* 请求体：``{"model", "input": {"messages": [...]}, "parameters": {...}}``；
* 响应：``{"output": {"choices": [{"message": {...}, "finish_reason": ...}]}, "usage": {...}}``；
* 流式：``parameters.stream = true``，SSE ``data: {json}`` 收尾；
* 工具调用：``message.tool_calls = [{"type": "function", "function": {"name", "arguments"}}]``，
  与 OpenAI 的差异是 ``arguments`` 是**已解析对象**而非 JSON 字符串；
* 鉴权：``Authorization: Bearer <key>``。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, ClassVar, Final

import httpx

from loopforge.core.logging import get_logger
from loopforge.providers.base import HttpProviderBase, ProviderBadResponseError
from loopforge.providers.openai_compat import (
    build_openai_messages,
    build_openai_tools,
    parse_tool_calls,
)
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
    Usage,
)

__all__ = [
    "DashScopeProvider",
    "DASHSCOPE_DEFAULT_BASE_URL",
    "DASHSCOPE_COMPAT_BASE_URL",
    "build_dashscope_body",
    "parse_dashscope_payload",
]

_LOG = get_logger(__name__)

#: 原生通道默认端点。
DASHSCOPE_DEFAULT_BASE_URL: Final[str] = "https://dashscope.aliyuncs.com/api/v1"

#: OpenAI 兼容通道端点（配置里覆写 ``base_url`` 即可切换）。
DASHSCOPE_COMPAT_BASE_URL: Final[str] = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

#: qwen3.8 系列上下文窗口（与 pricing.yaml 对齐）。
_CONTEXT_WINDOW: Final[int] = 262_144
_MAX_OUTPUT: Final[int] = 65_536


def build_dashscope_body(request: ChatRequest, *, stream: bool = False) -> dict[str, Any]:
    """构造 DashScope 原生请求体。

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
        if message.role == "tool":
            # DashScope 原生工具回执格式与 OpenAI 一致（tool_call_id + content）。
            item["tool_call_id"] = message.tool_call_id or ""
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments_json(),
                    },
                }
                for call in message.tool_calls
            ]
        messages.append(item)

    body: dict[str, Any] = {
        "model": request.model,
        "input": {"messages": messages},
        "parameters": {
            "temperature": request.temperature,
            "stream": stream,
            "result_format": "message",
        },
    }
    if request.max_tokens is not None:
        body["parameters"]["max_tokens"] = request.max_tokens
    if request.tools:
        body["input"]["tools"] = build_dashscope_tools(request.tools)
        body["input"]["tool_choice"] = request.tool_choice
    if request.stop:
        body["parameters"]["stop"] = list(request.stop)
    if request.seed is not None:
        body["parameters"]["seed"] = request.seed
    return body


def build_dashscope_tools(tools: Sequence[Any]) -> list[dict[str, Any]]:
    """把内部工具声明转换成 DashScope 原生 ``tools`` 数组。

    Args:
        tools: 内部工具声明（:class:`ToolSpec` 序列）。

    Returns:
        DashScope 格式工具列表。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in tools
    ]


def parse_dashscope_usage(payload: Mapping[str, Any]) -> Usage:
    """从 DashScope 原生响应解析用量。

    Args:
        payload: 响应体。

    Returns:
        用量对象。
    """
    raw = payload.get("usage")
    if not isinstance(raw, Mapping):
        return Usage()
    return Usage(
        prompt_tokens=int(raw.get("input_tokens", raw.get("prompt_tokens", 0)) or 0),
        completion_tokens=int(raw.get("output_tokens", raw.get("completion_tokens", 0)) or 0),
        cached_tokens=int(
            raw.get("input_tokens_details", {}).get("cached_tokens", 0) or 0
        )
        if isinstance(raw.get("input_tokens_details"), Mapping)
        else 0,
    )


def parse_dashscope_payload(
    payload: Mapping[str, Any], *, provider_name: str, model: str
) -> ChatResult:
    """把 DashScope 原生非流式响应解析成 :class:`ChatResult`。

    Args:
        payload: 响应体字典。
        provider_name: Provider 注册名。
        model: 模型名。

    Returns:
        解析结果。

    Raises:
        ProviderBadResponseError: 响应缺少 ``output`` 或内容无法解析。
    """
    output = payload.get("output")
    if not isinstance(output, Mapping):
        raise ProviderBadResponseError(
            f"{provider_name} 响应缺少 output",
            details={"provider": provider_name, "model": model, "payload_keys": list(payload)},
        )
    choices = iter_dicts(output.get("choices"))
    if not choices:
        raise ProviderBadResponseError(
            f"{provider_name} 响应缺少 output.choices",
            details={"provider": provider_name, "model": model},
        )
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ProviderBadResponseError(
            f"{provider_name} 响应缺少 output.choices[].message",
            details={"provider": provider_name, "model": model},
        )
    content = message.get("content")
    finish = choice.get("finish_reason")
    tool_calls = _parse_dashscope_tool_calls(message.get("tool_calls"))
    return ChatResult(
        content=content or "",
        tool_calls=tool_calls,
        usage=parse_dashscope_usage(payload),
        model=str(payload.get("model", model) or model),
        finish_reason=_normalize_dashscope_finish(finish),  # type: ignore[arg-type]
        raw=dict(payload),
    )


def _parse_dashscope_tool_calls(payload: Any) -> list[ToolCallRequest]:
    """解析 DashScope 原生工具调用。

    DashScope 原生响应的 ``arguments`` 是**对象**而非 JSON 字符串，需特判。

    Args:
        payload: ``message.tool_calls``。

    Returns:
        工具调用列表。
    """
    calls: list[ToolCallRequest] = []
    for index, item in enumerate(iter_dicts(payload)):
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
        calls.append(
            ToolCallRequest(
                id=str(item.get("id", "") or f"call_{index}"),
                name=name,
                arguments=arguments,
                raw_arguments=raw_text,
            )
        )
    return calls


def _normalize_dashscope_finish(finish: Any) -> str:
    """把 DashScope finish_reason 归一成内部五元组。"""
    mapping: dict[str, str] = {
        "stop": "stop",
        "length": "length",
        "tool_calls": "tool_calls",
        "content_filter": "content_filter",
    }
    return mapping.get(str(finish or ""), "stop")


class DashScopeProvider(HttpProviderBase):
    """阿里云百炼 DashScope Provider（Qwen 系，原生通道）。"""

    name: ClassVar[str] = "dashscope"
    aliases: ClassVar[tuple[str, ...]] = ("qwen", "aliyun", "bailian", "tongyi")
    kind: ClassVar[str] = "dashscope"
    default_base_url: ClassVar[str] = DASHSCOPE_DEFAULT_BASE_URL
    default_model: ClassVar[str] = "qwen3.8-max"
    free: ClassVar[bool] = False

    def __init__(self, config: ProviderConfigModel, clock: Any) -> None:
        """初始化 DashScope Provider。"""
        super().__init__(config, clock)

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Qwen 家族支持工具 / 流式 / JSON / seed。"""
        return ProviderCapabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_json_mode=True,
            supports_seed=True,
            context_window=_CONTEXT_WINDOW,
            max_output_tokens=_MAX_OUTPUT,
            supports_embedding=True,
        )

    def default_headers(self) -> dict[str, str]:
        """追加 Bearer 鉴权头。"""
        headers = super().default_headers()
        key = self.config.secret()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def chat(self, request: ChatRequest) -> ChatResult:
        """非流式对话补全（原生通道）。"""
        started = self.clock.monotonic()
        model = self.resolve_model(request.model)
        body = build_dashscope_body(request)
        body["model"] = model
        payload = await self.request_json(
            "POST",
            "/services/aigc/text-generation/generation",
            json_body=body,
            model=model,
            timeout_s=request.timeout_s,
        )
        result = parse_dashscope_payload(payload, provider_name=self.name, model=model)
        result.latency_ms = self.elapsed_ms(started)
        return result

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        """流式对话补全（原生通道 SSE）。"""
        started = self.clock.monotonic()
        model = self.resolve_model(request.model)
        body = build_dashscope_body(request, stream=True)
        body["model"] = model
        client = self.client()
        try:
            async with client.stream(
                "POST",
                "/services/aigc/text-generation/generation",
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
                async for event in aiter_sse(response):
                    if event.is_done:
                        break
                    data = event.json()
                    if not data:
                        continue
                    if data.get("usage") is not None:
                        usage = parse_dashscope_usage(data)
                    output = data.get("output")
                    if not isinstance(output, Mapping):
                        continue
                    for choice in iter_dicts(output.get("choices")):
                        message = choice.get("message")
                        if isinstance(message, Mapping):
                            content = message.get("content")
                            if isinstance(content, str) and content:
                                yield StreamChunk(delta=content, index=0, raw=data)
                            for call in _parse_dashscope_tool_calls(
                                message.get("tool_calls")
                            ):
                                yield StreamChunk(
                                    tool_call_delta=call, index=0, raw=data
                                )
                        finish = choice.get("finish_reason")
                        if finish is not None:
                            yield StreamChunk(
                                finish_reason=_normalize_dashscope_finish(finish),  # type: ignore[arg-type]
                                usage=usage,
                                index=0,
                                raw=data,
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
        """探活：GET ``/models``（DashScope 原生通道支持列模型）。"""
        return await self.probe("/models", expect=200)
