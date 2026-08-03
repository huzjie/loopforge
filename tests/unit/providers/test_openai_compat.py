"""OpenAI 兼容 Provider 及真实 Provider（httpx.MockTransport 打桩）单元测试。

覆盖：
* OpenAICompatProvider：chat / chat_stream / count_tokens / health_check / embed；
* DashScope 原生通道：chat / chat_stream / 工具调用；
* Anthropic Messages API：chat / chat_stream / 工具调用；
* DeepSeek：reasoning_content 剥离；
* Ollama：NDJSON 流式；
* 状态码归一（401 / 429 / 5xx → 对应错误码）。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from loopforge.core.clock import FrozenClock
from loopforge.core.errors import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderServerError,
)
from loopforge.providers.anthropic import AnthropicProvider
from loopforge.providers.dashscope import DashScopeProvider
from loopforge.providers.deepseek import DeepSeekProvider
from loopforge.providers.ollama import OllamaProvider
from loopforge.providers.openai_compat import OpenAICompatProvider
from loopforge.providers.types import (
    ChatRequest,
    EmbeddingRequest,
    Message,
    ProviderConfigModel,
    ToolSpec,
)

FROZEN = __import__(
    "loopforge.core.clock", fromlist=["FrozenClock"]
).FrozenClock


def _clock() -> FrozenClock:
    return FrozenClock()


def _cfg(
    name: str,
    kind: str,
    handler: Any,
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> ProviderConfigModel:
    return ProviderConfigModel(
        name=name,
        kind=kind,
        base_url=base_url,
        default_model=model,
        options={"transport": httpx.MockTransport(handler)},
    )


def _req(model: str = "gpt-4o-mini") -> ChatRequest:
    return ChatRequest(model=model, messages=[Message.user("hello")])


# ---------------------------------------------------------------------------
# OpenAI Compat
# ---------------------------------------------------------------------------


def test_openai_chat() -> None:
    """非流式 chat 解析。"""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        assert body["model"] == "gpt-4o-mini"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    provider = OpenAICompatProvider(
        _cfg("openai", "openai_compatible", handler), _clock()
    )
    import asyncio

    result = asyncio.run(provider.chat(_req()))
    assert result.content == "pong"
    assert result.finish_reason == "stop"
    assert result.usage.prompt_tokens == 5


def test_openai_chat_tool_calls() -> None:
    """工具调用解析。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "run_tests",
                                        "arguments": '{"path": "tests/"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            },
        )

    provider = OpenAICompatProvider(
        _cfg("openai", "openai_compatible", handler), _clock()
    )
    import asyncio

    result = asyncio.run(provider.chat(_req()))
    assert result.has_tool_calls
    assert result.tool_calls[0].name == "run_tests"
    assert result.tool_calls[0].arguments == {"path": "tests/"}
    assert result.finish_reason == "tool_calls"


def test_openai_stream() -> None:
    """SSE 流式解析。"""
    sse_payload = (
        'data: {"choices":[{"delta":{"content":"Hel"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sse_payload, headers={"Content-Type": "text/event-stream"})

    provider = OpenAICompatProvider(
        _cfg("openai", "openai_compatible", handler), _clock()
    )
    import asyncio

    async def collect() -> list[str]:
        return [c.delta async for c in provider.chat_stream(_req())]

    deltas = asyncio.run(collect())
    assert "".join(deltas) == "Hello"


def test_openai_health() -> None:
    """health_check 走 /models。"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"object": "list", "data": []})

    provider = OpenAICompatProvider(
        _cfg("openai", "openai_compatible", handler), _clock()
    )
    import asyncio

    health = asyncio.run(provider.health_check())
    assert health.healthy is True


def test_openai_embed() -> None:
    """embed 调用 /embeddings。"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(
            200,
            json={
                "data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}],
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            },
        )

    provider = OpenAICompatProvider(
        _cfg("openai", "openai_compatible", handler), _clock()
    )
    import asyncio

    result = asyncio.run(
        provider.embed(
            EmbeddingRequest(model="text-embedding-3-small", inputs=["hello"])
        )
    )
    assert result.dimension == 3
    assert result.vectors[0][0] == 0.1


def test_openai_status_mapping() -> None:
    """401/429/500 状态码归一。"""
    cases = [
        (401, ProviderAuthError),
        (429, ProviderRateLimitError),
        (500, ProviderServerError),
    ]
    import asyncio

    for status, error_cls in cases:
        def handler(request: httpx.Request, _status: int = status) -> httpx.Response:
            return httpx.Response(_status, json={"error": {"message": "err"}})

        provider = OpenAICompatProvider(
            _cfg("openai", "openai_compatible", handler), _clock()
        )
        with pytest.raises(error_cls):
            asyncio.run(provider.chat(_req()))


def test_openai_count_tokens() -> None:
    """count_tokens 返回正整数。"""
    provider = OpenAICompatProvider(
        _cfg("openai", "openai_compatible", lambda r: httpx.Response(200, json={})),
        _clock(),
    )
    assert provider.count_tokens([Message.user("hello world")], "gpt-4o-mini") > 0


def test_openai_auth_header() -> None:
    """带 Key 时追加 Authorization 头。"""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={
                "choices": [{"index": 0, "message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {},
            },
        )

    cfg = _cfg("openai", "openai_compatible", handler)
    cfg.api_key = __import__("pydantic", fromlist=["SecretStr"]).SecretStr("sk-test")
    provider = OpenAICompatProvider(cfg, _clock())
    import asyncio

    asyncio.run(provider.chat(_req()))
    assert seen["auth"] == "Bearer sk-test"


# ---------------------------------------------------------------------------
# DashScope
# ---------------------------------------------------------------------------


def test_dashscope_chat() -> None:
    """DashScope 原生通道非流式。"""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        assert body["model"] == "qwen3.8-max"
        assert body["input"]["messages"][0]["content"] == "hello"
        return httpx.Response(
            200,
            json={
                "output": {
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "你好"},
                            "finish_reason": "stop",
                        }
                    ]
                },
                "usage": {"input_tokens": 5, "output_tokens": 3},
                "model": "qwen3.8-max",
            },
        )

    provider = DashScopeProvider(
        _cfg("dashscope", "dashscope", handler, model="qwen3.8-max"),
        _clock(),
    )
    import asyncio

    result = asyncio.run(provider.chat(_req("qwen3.8-max")))
    assert result.content == "你好"
    assert result.usage.prompt_tokens == 5


def test_dashscope_tool_calls() -> None:
    """DashScope 原生工具调用（arguments 为对象）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "type": "function",
                                        "function": {
                                            "name": "search_code",
                                            "arguments": {"query": "def main"},
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
                "usage": {"input_tokens": 2, "output_tokens": 4},
            },
        )

    provider = DashScopeProvider(
        _cfg("dashscope", "dashscope", handler, model="qwen3.8-max"),
        _clock(),
    )
    import asyncio

    result = asyncio.run(provider.chat(_req("qwen3.8-max")))
    assert result.has_tool_calls
    assert result.tool_calls[0].name == "search_code"
    assert result.tool_calls[0].arguments == {"query": "def main"}


def test_dashscope_stream() -> None:
    """DashScope 原生流式。"""
    sse = (
        'data: {"output":{"choices":[{"message":{"content":"你"},"finish_reason":null}]}}\n\n'
        'data: {"output":{"choices":[{"message":{"content":"好"},"finish_reason":null}]}}\n\n'
        'data: {"output":{"choices":[{"message":{},"finish_reason":"stop"}]},"usage":{"input_tokens":1,"output_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sse, headers={"Content-Type": "text/event-stream"})

    provider = DashScopeProvider(
        _cfg("dashscope", "dashscope", handler, model="qwen3.8-max"),
        _clock(),
    )
    import asyncio

    async def collect() -> str:
        parts = []
        async for c in provider.chat_stream(_req("qwen3.8-max")):
            parts.append(c.delta)
        return "".join(parts)

    assert asyncio.run(collect()) == "你好"


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def test_anthropic_chat() -> None:
    """Anthropic Messages API 非流式。"""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        assert body["max_tokens"] > 0
        assert request.headers.get("anthropic-version") == "2023-06-01"
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Claude 回复"}],
                "model": "claude-sonnet-4-5",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 4},
            },
        )

    provider = AnthropicProvider(
        _cfg("anthropic", "anthropic", handler, model="claude-sonnet-4-5"),
        _clock(),
    )
    import asyncio

    result = asyncio.run(provider.chat(_req("claude-sonnet-4-5")))
    assert result.content == "Claude 回复"
    assert result.finish_reason == "stop"


def test_anthropic_tool_use() -> None:
    """Anthropic tool_use 块解析。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_01",
                        "name": "run_tests",
                        "input": {"path": "tests/"},
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            },
        )

    provider = AnthropicProvider(
        _cfg("anthropic", "anthropic", handler, model="claude-sonnet-4-5"),
        _clock(),
    )
    import asyncio

    result = asyncio.run(provider.chat(_req("claude-sonnet-4-5")))
    assert result.has_tool_calls
    assert result.tool_calls[0].name == "run_tests"
    assert result.finish_reason == "tool_calls"


def test_anthropic_stream() -> None:
    """Anthropic 流式（content_block_delta）。"""
    sse = (
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hel"}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"lo"}}\n\n'
        "event: message_delta\n"
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"input_tokens":1,"output_tokens":2}}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sse, headers={"Content-Type": "text/event-stream"})

    provider = AnthropicProvider(
        _cfg("anthropic", "anthropic", handler, model="claude-sonnet-4-5"),
        _clock(),
    )
    import asyncio

    async def collect() -> str:
        parts = []
        async for c in provider.chat_stream(_req("claude-sonnet-4-5")):
            parts.append(c.delta)
        return "".join(parts)

    assert asyncio.run(collect()) == "Hello"


# ---------------------------------------------------------------------------
# DeepSeek
# ---------------------------------------------------------------------------


def test_deepseek_reasoning_stripped() -> None:
    """DeepSeek 非流式 reasoning_content 剥离。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "最终答案",
                            "reasoning_content": "先思考…",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 6},
            },
        )

    provider = DeepSeekProvider(
        _cfg("deepseek", "deepseek", handler, model="deepseek-chat"),
        _clock(),
    )
    import asyncio

    result = asyncio.run(provider.chat(_req("deepseek-chat")))
    assert result.content == "最终答案"
    assert "思考" not in result.content
    assert "reasoning_content" in result.raw["choices"][0]["message"]


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


def test_ollama_chat() -> None:
    """Ollama 非流式。"""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        assert body["model"] == "qwen3.8-27b"
        return httpx.Response(
            200,
            json={
                "model": "qwen3.8-27b",
                "message": {"role": "assistant", "content": "本地回复"},
                "done": True,
                "prompt_eval_count": 5,
                "eval_count": 3,
            },
        )

    provider = OllamaProvider(
        _cfg("ollama", "ollama", handler, model="qwen3.8-27b"),
        _clock(),
    )
    import asyncio

    result = asyncio.run(provider.chat(_req("qwen3.8-27b")))
    assert result.content == "本地回复"
    assert result.usage.prompt_tokens == 5


def test_ollama_stream_ndjson() -> None:
    """Ollama NDJSON 流式。"""
    lines = "\n".join(
        [
            json.dumps({"model": "qwen3.8-27b", "message": {"content": "你"}, "done": False}),
            json.dumps({"model": "qwen3.8-27b", "message": {"content": "好"}, "done": False}),
            json.dumps(
                {
                    "model": "qwen3.8-27b",
                    "message": {"content": ""},
                    "done": True,
                    "prompt_eval_count": 2,
                    "eval_count": 4,
                }
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=lines, headers={"Content-Type": "application/x-ndjson"})

    provider = OllamaProvider(
        _cfg("ollama", "ollama", handler, model="qwen3.8-27b"),
        _clock(),
    )
    import asyncio

    async def collect() -> str:
        parts = []
        async for c in provider.chat_stream(_req("qwen3.8-27b")):
            parts.append(c.delta)
        return "".join(parts)

    assert asyncio.run(collect()) == "你好"


def test_ollama_free() -> None:
    """Ollama 零成本。"""
    assert OllamaProvider.free is True
