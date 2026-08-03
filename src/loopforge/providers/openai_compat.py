"""OpenAI 兼容协议 Provider —— 官方 / vLLM / LM Studio / 任意兼容网关。

实现要点
--------
1. 基于 :class:`~loopforge.providers.base.HttpProviderBase`，POST ``/chat/completions``；
2. 请求体按 OpenAI 官方结构构造，``messages`` / ``tools`` 由
   :func:`build_openai_messages` 与 :func:`build_openai_tools` 负责；
3. 响应解析覆盖三种形态：普通 JSON、``choices[0].message.tool_calls``、
   ``stream=true`` 的 SSE 流（``data: [DONE]`` 收尾）；
4. 认证头：``Authorization: Bearer <key>``；未配 Key 时仍可访问本地网关；
5. 模型名映射：``qwen3.8-max`` 等非 OpenAI 模型由上游网关自行翻译，
   本 Provider 不做任何改写。

测试打桩
--------
通过 ``config.options["transport"]`` 注入 ``httpx.MockTransport``::

    cfg = ProviderConfigModel(name="openai", options={"transport": transport})
    provider = OpenAICompatProvider(cfg, FrozenClock())
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, ClassVar, Final

import httpx

from loopforge.core.logging import get_logger
from loopforge.providers.base import (
    DEFAULT_USER_AGENT,
    HttpProviderBase,
    ProviderBadResponseError,
)
from loopforge.providers.streaming import (
    SSE_DONE,
    ToolCallAccumulator,
    aiter_sse,
    collect_stream,
    iter_dicts,
)
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
    "OpenAICompatProvider",
    "build_openai_messages",
    "build_openai_tools",
    "parse_chat_payload",
    "parse_usage",
    "parse_tool_calls",
    "DEFAULT_OPENAI_BASE_URL",
]

_LOG = get_logger(__name__)

#: OpenAI 官方默认端点。
DEFAULT_OPENAI_BASE_URL: Final[str] = "https://api.openai.com/v1"

#: 模型名 → 能力（未知模型取保守默认，价目表只影响成本不影响行为）。
_CONTEXT_WINDOW_BY_MODEL: Final[dict[str, int]] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "o3": 200_000,
    "o4-mini": 200_000,
    "gpt-5": 400_000,
}

#: 兼容网关默认上下文窗口。
_DEFAULT_CONTEXT_WINDOW: Final[int] = 131_072

#: 兼容网关默认最大输出。
_DEFAULT_MAX_OUTPUT: Final[int] = 16_384


def build_openai_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """把内部消息转换成 OpenAI ``messages`` 数组。

    Args:
        messages: 内部消息序列。

    Returns:
        OpenAI 格式消息列表。
    """
    result: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role}
        if message.content is not None:
            item["content"] = message.content
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments_json(),
                    },
                }
                for index, call in enumerate(message.tool_calls)
            ]
        if message.role == "tool":
            item["tool_call_id"] = message.tool_call_id or ""
        if message.name and message.role != "tool":
            item["name"] = message.name
        result.append(item)
    return result


def build_openai_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """把内部工具声明转换成 OpenAI ``tools`` 数组。

    Args:
        tools: 内部工具声明。

    Returns:
        OpenAI 格式工具列表。
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


def parse_usage(payload: Mapping[str, Any]) -> Usage:
    """从 OpenAI 响应体解析用量。

    Args:
        payload: 响应体。

    Returns:
        用量对象。
    """
    raw = payload.get("usage")
    if not isinstance(raw, Mapping):
        return Usage()
    return Usage(
        prompt_tokens=int(raw.get("prompt_tokens", 0) or 0),
        completion_tokens=int(raw.get("completion_tokens", 0) or 0),
        cached_tokens=int(raw.get("prompt_tokens_details", {}).get("cached_tokens", 0) or 0)
        if isinstance(raw.get("prompt_tokens_details"), Mapping)
        else 0,
    )


def parse_tool_calls(payload: Any) -> list[ToolCallRequest]:
    """从 ``message.tool_calls`` 数组解析工具调用。

    Args:
        payload: ``choices[0].message.tool_calls``（可能为 ``None``）。

    Returns:
        解析出的工具调用列表。
    """
    calls: list[ToolCallRequest] = []
    for index, item in enumerate(iter_dicts(payload)):
        function = item.get("function")
        if not isinstance(function, Mapping):
            continue
        name = str(function.get("name", "") or "")
        arguments = str(function.get("arguments", "") or "")
        if not name:
            continue
        calls.append(
            ToolCallRequest.from_json_text(
                id=str(item.get("id", "") or f"call_{index}"),
                name=name,
                raw=arguments,
            )
        )
    return calls


def parse_chat_payload(
    payload: Mapping[str, Any],
    *,
    provider_name: str,
    model: str,
) -> ChatResult:
    """把 OpenAI 非流式响应体解析成 :class:`ChatResult`。

    Args:
        payload: 响应体字典。
        provider_name: Provider 注册名（错误上下文用）。
        model: 模型名。

    Returns:
        解析结果。

    Raises:
        ProviderBadResponseError: 响应缺少 ``choices`` 或内容无法解析。
    """
    choices = iter_dicts(payload.get("choices"))
    if not choices:
        raise ProviderBadResponseError(
            f"{provider_name} 响应缺少 choices",
            details={"provider": provider_name, "model": model, "payload_keys": list(payload)},
        )
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ProviderBadResponseError(
            f"{provider_name} 响应缺少 message",
            details={"provider": provider_name, "model": model},
        )
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        # 某些网关把 content 返回成对象数组（OpenAI 视觉格式）。
        content = _flatten_content(content)
    finish = choice.get("finish_reason")
    return ChatResult(
        content=content or "",
        tool_calls=parse_tool_calls(message.get("tool_calls")),
        usage=parse_usage(payload),
        model=str(payload.get("model", model) or model),
        finish_reason=_normalize_finish(finish),  # type: ignore[arg-type]
        raw=dict(payload),
    )


def _flatten_content(content: Any) -> str:
    """把 OpenAI 的 content 对象数组拍平成纯文本。

    Args:
        content: ``content`` 字段。

    Returns:
        文本。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def _normalize_finish(finish: Any) -> str:
    """把 OpenAI finish_reason 归一成内部五元组。"""
    mapping: dict[str, str] = {
        "stop": "stop",
        "length": "length",
        "tool_calls": "tool_calls",
        "function_call": "tool_calls",
        "content_filter": "content_filter",
    }
    return mapping.get(str(finish or ""), "stop")


class OpenAICompatProvider(HttpProviderBase):
    """OpenAI 兼容协议 Provider（官方 / vLLM / LM Studio / 任意网关）。"""

    name: ClassVar[str] = "openai"
    aliases: ClassVar[tuple[str, ...]] = ("openai_compat", "openai-compatible", "vllm")
    kind: ClassVar[str] = "openai_compatible"
    default_base_url: ClassVar[str] = DEFAULT_OPENAI_BASE_URL
    default_model: ClassVar[str] = "gpt-4o-mini"

    def __init__(self, config: ProviderConfigModel, clock: Any) -> None:
        """初始化 OpenAI 兼容 Provider。"""
        super().__init__(config, clock)
        self._embedding_path = str(config.option("embedding_path", "/embeddings"))

    # ------------------------------------------------------------------ 能力

    @property
    def capabilities(self) -> ProviderCapabilities:
        """OpenAI 家族支持工具 / 流式 / JSON / seed。"""
        model = self.config.default_model or ""
        return ProviderCapabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_json_mode=True,
            supports_seed=True,
            context_window=_CONTEXT_WINDOW_BY_MODEL.get(model, _DEFAULT_CONTEXT_WINDOW),
            max_output_tokens=_DEFAULT_MAX_OUTPUT,
            supports_embedding=bool(self.config.option("embedding_path")),
        )

    # ------------------------------------------------------------------ 请求

    def default_headers(self) -> dict[str, str]:
        """追加 Bearer 鉴权头。"""
        headers = super().default_headers()
        key = self.config.secret()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _build_body(self, request: ChatRequest, *, stream: bool = False) -> dict[str, Any]:
        """构造 OpenAI ``/chat/completions`` 请求体。"""
        body: dict[str, Any] = {
            "model": self.resolve_model(request.model),
            "messages": build_openai_messages(request.messages),
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.tools:
            body["tools"] = build_openai_tools(request.tools)
            body["tool_choice"] = request.tool_choice
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.stop:
            body["stop"] = list(request.stop)
        if request.seed is not None:
            body["seed"] = request.seed
        return body

    async def chat(self, request: ChatRequest) -> ChatResult:
        """非流式对话补全。"""
        started = self.clock.monotonic()
        model = self.resolve_model(request.model)
        payload = await self.request_json(
            "POST",
            "/chat/completions",
            json_body=self._build_body(request),
            model=model,
            timeout_s=request.timeout_s,
        )
        result = parse_chat_payload(payload, provider_name=self.name, model=model)
        result.latency_ms = self.elapsed_ms(started)
        return result

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        """流式对话补全（SSE）。"""
        started = self.clock.monotonic()
        model = self.resolve_model(request.model)
        client = self.client()
        try:
            async with client.stream(
                "POST",
                "/chat/completions",
                json=self._build_body(request, stream=True),
                headers=self.default_headers(),
                timeout=request.timeout_s,
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise self._map_status(
                        response.status_code,
                        body.decode("utf-8", errors="replace"),
                        model,
                    )
                accumulator = ToolCallAccumulator()
                usage = Usage()
                async for event in aiter_sse(response):
                    if event.is_done:
                        break
                    data = event.json()
                    if not data:
                        continue
                    if data.get("usage") is not None:
                        usage = parse_usage(data)
                    for choice in iter_dicts(data.get("choices")):
                        delta = choice.get("delta")
                        if not isinstance(delta, Mapping):
                            continue
                        text = delta.get("content")
                        if isinstance(text, str) and text:
                            yield StreamChunk(delta=text, index=0, raw=data)
                        for call in iter_dicts(delta.get("tool_calls")):
                            complete = accumulator.feed(call)
                            if complete is not None:
                                yield StreamChunk(
                                    tool_call_delta=complete, index=0, raw=data
                                )
                        finish = choice.get("finish_reason")
                        if finish is not None:
                            yield StreamChunk(
                                finish_reason=_normalize_finish(finish),  # type: ignore[arg-type]
                                usage=usage,
                                index=0,
                                raw=data,
                            )
                            return
                # SSE 流自然结束（``[DONE]`` 或 EOF）：补齐收尾 chunk。
                calls = accumulator.finish()
                final_reason = "tool_calls" if calls else "stop"
                yield StreamChunk(
                    finish_reason=final_reason,  # type: ignore[arg-type]
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

    def _map_status(self, status_code: int, body: str, model: str) -> Exception:
        """把流式请求的状态码映射成异常。"""
        from loopforge.providers.base import map_http_status

        return map_http_status(status_code, provider=self.name, model=model, body=body)

    def count_tokens(self, messages: Sequence[Message], model: str) -> int:
        """估算 prompt token 数。"""
        return count_messages(messages, model)

    async def health_check(self) -> ProviderHealth:
        """探活：GET ``/models``。"""
        return await self.probe("/models", expect=200)

    async def embed(self, request: Any) -> Any:
        """文本向量化（可选能力，需配置 ``embedding_path``）。"""
        from loopforge.providers.types import EmbeddingRequest, EmbeddingResult

        req = request if isinstance(request, EmbeddingRequest) else None
        if req is None:
            return await super().embed(request)  # type: ignore[arg-type]
        started = self.clock.monotonic()
        payload = await self.request_json(
            "POST",
            self._embedding_path,
            json_body={
                "model": req.model or self.resolve_model(None),
                "input": req.inputs,
            },
            model=req.model,
            timeout_s=req.timeout_s,
        )
        data = payload.get("data")
        vectors: list[list[float]] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, Mapping):
                    embedding = item.get("embedding")
                    if isinstance(embedding, list):
                        vectors.append([float(v) for v in embedding])
        return EmbeddingResult(
            vectors=vectors,
            model=str(payload.get("model", req.model or "")),
            usage=parse_usage(payload),
            latency_ms=self.elapsed_ms(started),
        )
