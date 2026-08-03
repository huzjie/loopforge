"""ProviderRouter / FailoverChain / retry 单元测试。

覆盖：
* Router 按角色解析（settings 驱动）；
* hint 过滤（require_tools / exclude / min_context_window）；
* FailoverChain 主备切换与熔断；
* 全部不可用抛 ``LF-PROVIDER-0705``；
* retry 模块的可重试 / 不可重试分类。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from loopforge.core.clock import FrozenClock
from loopforge.core.enums import RoleName
from loopforge.core.errors import (
    AllProvidersUnavailableError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
)
from loopforge.providers.registry import create_mock_provider, provider_registry
from loopforge.providers.retry import (
    NON_RETRYABLE_PROVIDER_ERRORS,
    RETRYABLE_PROVIDER_ERRORS,
    call_with_retry,
    should_retry_provider_error,
)
from loopforge.providers.router import FailoverChain, ProviderRouter
from loopforge.providers.types import (
    ChatRequest,
    Message,
    ProviderBinding,
    RoutingHint,
)



def _req() -> ChatRequest:
    return ChatRequest(
        model="mock-large",
        messages=[Message.user("hi")],
        metadata={"role": "CODER", "loop_index": "0", "task_key": "r"},
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


async def test_resolve_default_role(frozen_clock: Any) -> None:
    """按角色解析应返回 mock 绑定（settings 默认）。"""
    router = ProviderRouter(clock=frozen_clock)
    binding = await router.resolve(RoleName.CODER)
    assert binding.provider_name == "mock"
    assert binding.model == "mock-large"
    assert binding.source in {"settings", "default"}
    assert binding.provider is not None
    assert binding.role == RoleName.CODER


async def test_resolve_all_roles(frozen_clock: Any) -> None:
    """五个角色都应可解析。"""
    router = ProviderRouter(clock=frozen_clock)
    for role in RoleName:
        binding = await router.resolve(role)
        assert binding.provider_name
        assert binding.model
        assert binding.require_provider() is not None


async def test_resolve_hint_provider(frozen_clock: Any) -> None:
    """hint 显式指定 provider 时覆盖 settings。"""
    router = ProviderRouter(clock=frozen_clock)
    binding = await router.resolve(
        RoleName.CODER, RoutingHint(provider="openai", model="gpt-4o-mini")
    )
    assert binding.provider_name == "openai"
    assert binding.model == "gpt-4o-mini"
    assert binding.source == "hint"


async def test_resolve_hint_require_tools(frozen_clock: Any) -> None:
    """require_tools 应过滤掉不支持工具的 Provider。"""
    router = ProviderRouter(clock=frozen_clock)
    binding = await router.resolve(
        RoleName.CODER, RoutingHint(provider="mock", require_tools=True)
    )
    assert binding.provider_name == "mock"


async def test_resolve_hint_exclude(frozen_clock: Any) -> None:
    """exclude 列表应排除指定 Provider。"""
    router = ProviderRouter(clock=frozen_clock)
    # exclude 命中主 provider → 解析失败。
    with pytest.raises(ProviderError):
        await router.resolve(
            RoleName.CODER, RoutingHint(provider="mock", exclude=["mock"])
        )
    # exclude 未命中主 provider 时不影响主链。
    binding = await router.resolve(
        RoleName.CODER, RoutingHint(provider="mock", exclude=["openai"])
    )
    assert binding.provider_name == "mock"


async def test_binding_build_request(frozen_clock: Any) -> None:
    """build_request 应自动补 role metadata。"""
    router = ProviderRouter(clock=frozen_clock)
    binding = await router.resolve(RoleName.CODER)
    request = binding.build_request([Message.user("hello")], metadata={"run_id": "r1"})
    assert request.metadata["role"] == "CODER"
    assert request.metadata["run_id"] == "r1"
    assert request.model == binding.model


# ---------------------------------------------------------------------------
# FailoverChain
# ---------------------------------------------------------------------------


async def test_failover_to_fallback(frozen_clock: Any) -> None:
    """主 Provider 失败应切换到 fallback 并成功。"""
    primary = create_mock_provider(
        clock=frozen_clock, options={"faults": [{"kind": "server_error", "at_call": 1}]}
    )
    binding = ProviderBinding(
        role=RoleName.CODER,
        provider_name="mock",
        model="mock-large",
        provider=primary,
        fallbacks=(("mock", "mock-large"),),
    )
    router = ProviderRouter(clock=frozen_clock)
    chain = FailoverChain(router.breakers, clock=frozen_clock)
    result = await chain.run(binding, lambda p, m: p.chat(_req()))
    assert result.content
    assert len(chain.switch_reasons) == 1
    assert chain.switch_reasons[0].reason == "LF-PROVIDER-0704"


async def test_failover_all_unavailable(frozen_clock: Any) -> None:
    """无 fallback 且主 Provider 失败应抛 AllProvidersUnavailableError。"""
    primary = create_mock_provider(
        clock=frozen_clock, options={"faults": [{"kind": "server_error", "at_call": 1}]}
    )
    binding = ProviderBinding(
        role=RoleName.CODER,
        provider_name="mock",
        model="mock-large",
        provider=primary,
        fallbacks=(),
    )
    chain = FailoverChain({}, clock=frozen_clock)
    with pytest.raises(AllProvidersUnavailableError) as excinfo:
        await chain.run(binding, lambda p, m: p.chat(_req()))
    assert excinfo.value.code == "LF-PROVIDER-0705"
    assert excinfo.value.retryable is True


async def test_failover_non_retryable_not_switched(frozen_clock: Any) -> None:
    """鉴权类错误（retryable=False）不应切换 fallback，直接抛出。"""
    primary = create_mock_provider(
        clock=frozen_clock, options={"faults": [{"kind": "auth", "at_call": 1}]}
    )
    binding = ProviderBinding(
        role=RoleName.CODER,
        provider_name="mock",
        model="mock-large",
        provider=primary,
        fallbacks=(("mock", "mock-large"),),
    )
    chain = FailoverChain({}, clock=frozen_clock)
    with pytest.raises(ProviderAuthError):
        await chain.run(binding, lambda p, m: p.chat(_req()))
    assert chain.switch_reasons == []


async def test_circuit_breaker_opens(frozen_clock: Any) -> None:
    """连续失败应打开熔断器，后续调用被直接拒绝。"""
    from loopforge.core.resilience import CircuitBreaker

    breaker = CircuitBreaker(
        name="provider:mock",
        failure_threshold=2,
        recovery_timeout_s=60,
        clock=frozen_clock,
    )
    chain = FailoverChain({"mock": breaker}, clock=frozen_clock)
    binding = ProviderBinding(
        role=RoleName.CODER,
        provider_name="mock",
        model="mock-large",
        provider=create_mock_provider(
            clock=frozen_clock,
            options={"faults": [{"kind": "server_error", "at_call": 1, "times": -1}]},
        ),
        fallbacks=(),
    )
    # 两次失败后熔断打开。
    for _ in range(2):
        with pytest.raises(AllProvidersUnavailableError):
            await chain.run(binding, lambda p, m: p.chat(_req()))
    assert breaker.state.value == "OPEN"
    # 熔断打开后：第三次调用被熔断器拒绝（circuit_open），抛 0705。
    with pytest.raises(AllProvidersUnavailableError):
        await chain.run(binding, lambda p, m: p.chat(_req()))


async def test_circuit_breaker_recovery(frozen_clock: Any) -> None:
    """恢复超时后应进入半开态并可重试。"""
    from loopforge.core.resilience import CircuitBreaker

    assert isinstance(frozen_clock, FrozenClock)
    breaker = CircuitBreaker(
        name="provider:mock",
        failure_threshold=1,
        recovery_timeout_s=5,
        clock=frozen_clock,
    )
    chain = FailoverChain({"mock": breaker}, clock=frozen_clock)
    binding = ProviderBinding(
        role=RoleName.CODER,
        provider_name="mock",
        model="mock-large",
        provider=create_mock_provider(
            clock=frozen_clock,
            options={"faults": [{"kind": "server_error", "at_call": 1, "times": -1}]},
        ),
        fallbacks=(),
    )
    with pytest.raises(AllProvidersUnavailableError):
        await chain.run(binding, lambda p, m: p.chat(_req()))
    assert breaker.state.value == "OPEN"
    # 推进时钟超过恢复期。
    frozen_clock.advance(6)
    with pytest.raises(AllProvidersUnavailableError):
        await chain.run(binding, lambda p, m: p.chat(_req()))
    # 半开态下试探失败会再次跳闸。
    assert breaker.state.value == "OPEN"


# ---------------------------------------------------------------------------
# retry
# ---------------------------------------------------------------------------


async def test_should_retry_provider_error() -> None:
    """可重试分类正确。"""
    assert should_retry_provider_error(ProviderRateLimitError("x", details={})) is True
    assert should_retry_provider_error(ProviderServerError("x", details={})) is True
    assert should_retry_provider_error(ProviderAuthError("x", details={})) is False
    assert should_retry_provider_error(ValueError("x")) is False


async def test_call_with_retry_success_after_failures() -> None:
    """重试后成功。"""
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderRateLimitError("limit", details={})
        return "ok"

    result = await call_with_retry(
        flaky, provider="mock", max_attempts=4, base_s=0.01, jitter_ratio=0.0
    )
    assert result == "ok"
    assert calls["n"] == 3


async def test_call_with_retry_non_retryable_immediate() -> None:
    """不可重试错误应立即抛出，不重试。"""
    calls = {"n": 0}

    async def auth_fail() -> str:
        calls["n"] += 1
        raise ProviderAuthError("auth", details={})

    with pytest.raises(ProviderAuthError):
        await call_with_retry(
            auth_fail, provider="mock", max_attempts=4, base_s=0.01, jitter_ratio=0.0
        )
    assert calls["n"] == 1


async def test_call_with_retry_exhausts() -> None:
    """重试耗尽后抛出最后一次错误。"""
    calls = {"n": 0}

    async def always_fail() -> str:
        calls["n"] += 1
        raise ProviderServerError("server", details={})

    with pytest.raises(ProviderServerError):
        await call_with_retry(
            always_fail, provider="mock", max_attempts=3, base_s=0.01, jitter_ratio=0.0
        )
    assert calls["n"] == 3


def test_retry_error_tuple_contents() -> None:
    """错误分类元组内容正确。"""
    assert ProviderRateLimitError in RETRYABLE_PROVIDER_ERRORS
    assert ProviderServerError in RETRYABLE_PROVIDER_ERRORS
    assert ProviderAuthError in NON_RETRYABLE_PROVIDER_ERRORS


def test_registry_has_all_builtin() -> None:
    """内置 Provider 全部注册。"""
    names = provider_registry.list_names()
    assert {"mock", "openai", "dashscope", "anthropic", "deepseek", "ollama"} <= set(
        names
    )
