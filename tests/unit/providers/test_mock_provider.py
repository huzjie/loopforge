"""MockProvider 单元测试 —— 确定性 / 代码生成 / 工具调用 / 流式 / 故障注入。

覆盖 DoD（``TASK_BREAKDOWN`` §4.5）：
* 连续 100 次相同入参输出完全一致（含 tool_calls 顺序）；
* 生成的代码语法正确且可被实际执行；
* 五类故障注入可触发。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from loopforge.core.errors import (
    AllProvidersUnavailableError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)
from loopforge.providers.base import ProviderContentFilteredError
from loopforge.providers.registry import create_mock_provider
from loopforge.providers.types import ChatRequest, Message



def _run(coro: Any) -> Any:
    """测试内统一的事件循环入口。"""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 确定性
# ---------------------------------------------------------------------------


async def test_deterministic_100_times(mock_code_request: ChatRequest) -> None:
    """相同入参连续 100 次输出字节级一致。"""
    provider = create_mock_provider()
    first = await provider.chat(mock_code_request)
    for _ in range(100):
        again = await provider.chat(mock_code_request)
        assert again.content == first.content
        assert again.tool_calls == first.tool_calls
        assert again.usage == first.usage
        assert again.finish_reason == first.finish_reason


async def test_different_metadata_yields_different_output(
    mock_chat_request: ChatRequest,
) -> None:
    """不同 loop_index 应产出不同输出（确定性派生）。"""
    provider = create_mock_provider()
    req_a = mock_chat_request.model_copy(
        update={"metadata": {**mock_chat_request.metadata, "loop_index": "0"}}
    )
    req_b = mock_chat_request.model_copy(
        update={"metadata": {**mock_chat_request.metadata, "loop_index": "1"}}
    )
    out_a = await provider.chat(req_a)
    out_b = await provider.chat(req_b)
    assert out_a.content != out_b.content


async def test_seed_reproducible_across_instances(
    mock_code_request: ChatRequest,
) -> None:
    """不同实例、相同配置与请求也应输出一致。"""
    provider_a = create_mock_provider()
    provider_b = create_mock_provider()
    out_a = await provider_a.chat(mock_code_request)
    out_b = await provider_b.chat(mock_code_request)
    assert out_a.content == out_b.content


# ---------------------------------------------------------------------------
# 代码生成
# ---------------------------------------------------------------------------


async def test_code_generation_is_runnable(mock_code_request: ChatRequest) -> None:
    """MockProvider 应产出真实可执行的 Python 代码。"""
    provider = create_mock_provider()
    result = await provider.chat(mock_code_request)
    assert "def factorial" in result.content
    assert "return" in result.content
    # 代码语法必须合法 —— 用 compile 校验。
    compile(result.content, "<mock>", "exec")


async def test_codegen_factorial_matches() -> None:
    """生成阶乘函数应能正确计算。"""
    from loopforge.providers.mock import MockCodegen

    codegen = MockCodegen()
    result = codegen.generate("写一个 def factorial(n) 计算阶乘", seed=42)
    namespace: dict[str, Any] = {}
    exec(result.code, namespace)  # noqa: S102 —— 测试专用
    assert namespace["factorial"](5) == 120
    assert namespace["factorial"](0) == 1


async def test_codegen_diff_contains_hunks() -> None:
    """unified diff 应包含 +/- 行。"""
    from loopforge.providers.mock import MockCodegen

    result = MockCodegen().generate("实现 fibonacci 函数", seed=7)
    assert "+def " in result.diff
    assert "-def " in result.diff


# ---------------------------------------------------------------------------
# 工具调用
# ---------------------------------------------------------------------------


async def test_tool_call_required(mock_tool_request: ChatRequest) -> None:
    """tool_choice=required 时返回结构化工具调用。"""
    provider = create_mock_provider()
    result = await provider.chat(mock_tool_request)
    assert result.has_tool_calls
    assert result.tool_calls[0].name == "run_tests"
    assert "path" in result.tool_calls[0].arguments
    assert result.finish_reason == "tool_calls"


async def test_tool_call_auto_mentions_tool(mock_tool_spec: Any) -> None:
    """auto 模式下提到工具名才触发。"""
    provider = create_mock_provider()
    req = ChatRequest(
        model="mock-large",
        messages=[Message.user("请使用 run_tests 检查一下。")],
        tools=[mock_tool_spec],
        tool_choice="auto",
        metadata={"role": "TESTER", "loop_index": "0", "task_key": "t"},
    )
    result = await provider.chat(req)
    assert result.has_tool_calls


async def test_tool_call_none(mock_tool_request: ChatRequest) -> None:
    """tool_choice=none 时即使提到工具也不触发。"""
    provider = create_mock_provider()
    req = mock_tool_request.model_copy(update={"tool_choice": "none"})
    result = await provider.chat(req)
    assert not result.has_tool_calls


# ---------------------------------------------------------------------------
# 多轮对话
# ---------------------------------------------------------------------------


async def test_multi_turn_uses_history(mock_chat_request: ChatRequest) -> None:
    """多轮对话应引用历史 assistant 消息。"""
    provider = create_mock_provider()
    history = ChatRequest(
        model="mock-large",
        messages=[
            Message.system("你是助手。"),
            Message.user("第一轮问题"),
            Message.assistant("第一轮回答"),
            Message.user("第二轮问题"),
        ],
        metadata={"role": "CODER", "loop_index": "2", "task_key": "multi"},
    )
    result = await provider.chat(history)
    assert "2" in result.content  # 轮数信息会被写入确定性文本


# ---------------------------------------------------------------------------
# 流式
# ---------------------------------------------------------------------------


async def test_stream_equals_chat(mock_chat_request: ChatRequest) -> None:
    """流式合并结果应与非流式一致。"""
    provider = create_mock_provider()
    chat_result = await provider.chat(mock_chat_request)
    chunks = [chunk async for chunk in provider.chat_stream(mock_chat_request)]
    text = "".join(c.delta for c in chunks if c.delta)
    assert text == chat_result.content
    assert chunks[-1].is_final
    assert chunks[-1].usage is not None


async def test_stream_tool_call(mock_tool_request: ChatRequest) -> None:
    """流式应吐工具调用增量。"""
    provider = create_mock_provider()
    tool_calls = [
        chunk.tool_call_delta
        for chunk in [c async for c in provider.chat_stream(mock_tool_request)]
        if chunk.tool_call_delta is not None
    ]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "run_tests"


# ---------------------------------------------------------------------------
# 故障注入
# ---------------------------------------------------------------------------


async def test_fault_rate_limit() -> None:
    """限流故障可注入且 retryable=True。"""
    provider = create_mock_provider(
        options={"faults": [{"kind": "rate_limit", "at_call": 1, "times": 1}]}
    )
    req = ChatRequest(model="mock-large", messages=[Message.user("hi")])
    with pytest.raises(ProviderRateLimitError) as excinfo:
        await provider.chat(req)
    assert excinfo.value.retryable is True


async def test_fault_timeout() -> None:
    """超时故障可注入且 retryable=True。"""
    provider = create_mock_provider(
        options={"faults": [{"kind": "timeout", "at_call": 1}]}
    )
    req = ChatRequest(model="mock-large", messages=[Message.user("hi")])
    with pytest.raises(ProviderTimeoutError):
        await provider.chat(req)


async def test_fault_server_error() -> None:
    """服务端错误故障可注入且 retryable=True。"""
    provider = create_mock_provider(
        options={"faults": [{"kind": "server_error", "at_call": 1}]}
    )
    req = ChatRequest(model="mock-large", messages=[Message.user("hi")])
    with pytest.raises(ProviderServerError):
        await provider.chat(req)


async def test_fault_auth() -> None:
    """鉴权故障可注入且 retryable=False。"""
    provider = create_mock_provider(
        options={"faults": [{"kind": "auth", "at_call": 1}]}
    )
    req = ChatRequest(model="mock-large", messages=[Message.user("hi")])
    with pytest.raises(ProviderAuthError) as excinfo:
        await provider.chat(req)
    assert excinfo.value.retryable is False


async def test_fault_content_filter() -> None:
    """内容拦截故障可注入。"""
    provider = create_mock_provider(
        options={"faults": [{"kind": "content_filter", "at_call": 1}]}
    )
    req = ChatRequest(model="mock-large", messages=[Message.user("hi")])
    with pytest.raises(ProviderContentFilteredError):
        await provider.chat(req)


async def test_fault_truncate(mock_chat_request: ChatRequest) -> None:
    """截断故障应产出 length finish_reason。"""
    provider = create_mock_provider(
        options={"faults": [{"kind": "truncate", "at_call": 1}]}
    )
    result = await provider.chat(mock_chat_request)
    assert result.finish_reason == "length"


async def test_fault_times_limit() -> None:
    """times 限制：只触发指定次数。"""
    provider = create_mock_provider(
        options={"faults": [{"kind": "server_error", "at_call": 1, "times": 2}]}
    )
    req = ChatRequest(model="mock-large", messages=[Message.user("hi")])
    with pytest.raises(ProviderServerError):
        await provider.chat(req)
    with pytest.raises(ProviderServerError):
        await provider.chat(req)
    # 第三次恢复正常。
    result = await provider.chat(req)
    assert result.content


async def test_fault_model_scoped() -> None:
    """model 限定：只有指定模型触发。"""
    provider = create_mock_provider(
        options={"faults": [{"kind": "server_error", "at_call": 1, "model": "mock-large"}]}
    )
    good = ChatRequest(model="mock-small", messages=[Message.user("hi")])
    result = await provider.chat(good)
    assert result.content
    bad = ChatRequest(model="mock-large", messages=[Message.user("hi")])
    with pytest.raises(ProviderServerError):
        await provider.chat(bad)


# ---------------------------------------------------------------------------
# 计数与健康
# ---------------------------------------------------------------------------


async def test_count_tokens(mock_chat_request: ChatRequest) -> None:
    """count_tokens 应返回正整数。"""
    provider = create_mock_provider()
    count = provider.count_tokens(mock_chat_request.messages, "mock-large")
    assert count > 0


async def test_health_check(mock_provider: Any) -> None:
    """Mock 健康检查恒为健康。"""
    health = await mock_provider.health_check()
    assert health.healthy is True
    assert health.name == "mock"


async def test_close_idempotent(mock_provider: Any) -> None:
    """close 幂等。"""
    await mock_provider.close()
    await mock_provider.close()
    assert mock_provider.closed
