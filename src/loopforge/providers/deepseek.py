"""DeepSeek Provider —— 含 ``reasoning_content`` 思维链处理。

DeepSeek API 与 OpenAI 兼容协议同构，差异集中在两点：

1. **思维链**：非流式响应里 ``choices[0].message.reasoning_content`` 携带
   「深度思考」文本；流式响应里 ``choices[0].delta.reasoning_content`` 增量吐出。
   默认**不返回给上层**（避免污染 tool_calls 的对话），但完整保留在
   ``ChatResult.raw`` 中，供审计与回放；
2. **工具调用**：DeepSeek 的 ``tool_calls`` 与 OpenAI 完全一致。

因此本类继承 :class:`OpenAICompatProvider` 仅覆写响应解析与流式增量处理。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, ClassVar, Final

import httpx

from loopforge.core.logging import get_logger
from loopforge.providers.openai_compat import (
    OpenAICompatProvider,
    parse_chat_payload,
    parse_usage,
)
from loopforge.providers.streaming import ToolCallAccumulator, aiter_sse, iter_dicts
from loopforge.providers.token_counter import count_messages
from loopforge.providers.types import (
    ChatRequest,
    ChatResult,
    Message,
    ProviderCapabilities,
    ProviderHealth,
    StreamChunk,
    Usage,
)

__all__ = [
    "DeepSeekProvider",
    "DEFAULT_DEEPSEEK_BASE_URL",
    "REASONING_FIELD",
    "split_reasoning_content",
]

_LOG = get_logger(__name__)

#: DeepSeek 默认端点。
DEFAULT_DEEPSEEK_BASE_URL: Final[str] = "https://api.deepseek.com"

#: 思维链字段名（非流式 message / 流式 delta 通用）。
REASONING_FIELD: Final[str] = "reasoning_content"

#: 默认上下文窗口。
_CONTEXT_WINDOW: Final[int] = 131_072
_MAX_OUTPUT: Final[int] = 8_192


def split_reasoning_content(message: Mapping[str, Any]) -> tuple[str, str]:
    """从 DeepSeek message/delta 中拆出 ``(content, reasoning_content)``。

    Args:
        message: ``choices[0].message`` 或 ``choices[0].delta``。

    Returns:
        ``(文本内容, 思维链文本)``。
    """
    content = message.get("content")
    reasoning = message.get(REASONING_FIELD)
    text = content if isinstance(content, str) else ""
    chain = reasoning if isinstance(reasoning, str) else ""
    return text, chain


class DeepSeekProvider(OpenAICompatProvider):
    """DeepSeek Provider（OpenAI 兼容 + reasoning_content 处理）。"""

    name: ClassVar[str] = "deepseek"
    aliases: ClassVar[tuple[str, ...]] = ("deepseek-chat", "deepseek-reasoner")
    kind: ClassVar[str] = "deepseek"
    default_base_url: ClassVar[str] = DEFAULT_DEEPSEEK_BASE_URL
    default_model: ClassVar[str] = "deepseek-chat"
    free: ClassVar[bool] = False

    @property
    def capabilities(self) -> ProviderCapabilities:
        """DeepSeek 支持工具 / 流式 / JSON，不支持 seed。"""
        return ProviderCapabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_json_mode=True,
            supports_seed=False,
            context_window=_CONTEXT_WINDOW,
            max_output_tokens=_MAX_OUTPUT,
            supports_embedding=False,
        )

    async def chat(self, request: ChatRequest) -> ChatResult:
        """非流式对话补全（剥离 reasoning_content）。"""
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
        """流式对话补全（跳过 reasoning_content 增量）。"""
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
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise self._map_status(
                        response.status_code, body, model
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
                        text, reasoning = split_reasoning_content(delta)
                        if text:
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
                                finish_reason=_normalize_deepseek_finish(finish),  # type: ignore[arg-type]
                                usage=usage,
                                index=0,
                                raw=data,
                            )
                            return
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

    def count_tokens(self, messages: Sequence[Message], model: str) -> int:
        """估算 prompt token 数。"""
        return count_messages(messages, model)

    async def health_check(self) -> ProviderHealth:
        """探活：GET ``/models``。"""
        return await self.probe("/models", expect=200)


def _normalize_deepseek_finish(finish: Any) -> str:
    """把 DeepSeek finish_reason 归一成内部五元组。"""
    mapping: dict[str, str] = {
        "stop": "stop",
        "length": "length",
        "tool_calls": "tool_calls",
        "content_filter": "content_filter",
    }
    return mapping.get(str(finish or ""), "stop")
