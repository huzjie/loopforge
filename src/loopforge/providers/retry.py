"""Provider 调用重试与退避 —— 区分可重试 / 不可重试错误。

铁律（``TASK_BREAKDOWN`` §4.4-5）
--------------------------------
**Provider 层本身不做重试**。重试统一由本模块与 :mod:`loopforge.core.resilience`
编排，避免双层重试把成本放大成平方级。

本模块提供：
* :func:`provider_retry_policy` —— 按配置构造 :class:`RetryPolicy`，明确列出
  可重试 / 不可重试的 Provider 异常类型；
* :func:`should_retry_provider_error` —— 单条错误的可重试判断；
* :func:`call_with_retry` —— 一键包装「重试 + 退避 + 熔断」的调用入口，
  供 WP4 编排引擎直接使用。

错误分类（与 ``base.map_http_status`` 保持一致）
------------------------------------------------
==============  =====================  ==========
错误              retryable             说明
==============  =====================  ==========
认证 / 鉴权        否                    修 Key 才有意义
模型不支持         否                    换模型才有意义
内容被拦截         否                    改 prompt 才有意义
限流              是                    退避后重试
超时              是                    退避后重试
服务端错误         是                    退避后重试
坏响应             是                    解析失败可能为瞬时
全部不可用         否                    由 FailoverChain 处理，不原地重试
==============  =====================  ==========
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Final, TypeVar

from loopforge.core.errors import (
    AllProvidersUnavailableError,
    LoopForgeError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)
from loopforge.providers.base import (
    ProviderContentFilteredError,
    ProviderModelUnsupportedError,
)
from loopforge.core.resilience import RetryPolicy, retry_async

__all__ = [
    "RETRYABLE_PROVIDER_ERRORS",
    "NON_RETRYABLE_PROVIDER_ERRORS",
    "should_retry_provider_error",
    "provider_retry_policy",
    "call_with_retry",
    "ProviderRetryContext",
]

T = TypeVar("T")

#: 可重试的 Provider 异常（退避后重试有意义）。
RETRYABLE_PROVIDER_ERRORS: Final[tuple[type[BaseException], ...]] = (
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderServerError,
)

#: 不可重试的 Provider 异常（原地重试无意义）。
NON_RETRYABLE_PROVIDER_ERRORS: Final[tuple[type[BaseException], ...]] = (
    ProviderAuthError,
    ProviderModelUnsupportedError,
    ProviderContentFilteredError,
    AllProvidersUnavailableError,
)


def should_retry_provider_error(exc: BaseException) -> bool:
    """判断一个 Provider 异常是否可重试。

    规则：
    1. ``LoopForgeError.retryable`` 字段说了算（``base.map_http_status`` 已正确设置）；
    2. 非 LoopForge 异常（如偶发 ``ValueError``）默认不重试，避免掩盖 bug。

    Args:
        exc: 捕获到的异常。

    Returns:
        可重试返回 ``True``。
    """
    if isinstance(exc, LoopForgeError):
        return bool(exc.retryable)
    return False


def provider_retry_policy(
    *,
    max_attempts: int = 4,
    base_s: float = 1.0,
    max_s: float = 30.0,
    jitter_ratio: float = 0.2,
) -> RetryPolicy:
    """按默认值构造 Provider 专用的 :class:`RetryPolicy`。

    Args:
        max_attempts: 总尝试次数（含首次）。
        base_s: 基础退避秒数。
        max_s: 退避上限秒数。
        jitter_ratio: 抖动比例。

    Returns:
        配置好的重试策略。
    """
    return RetryPolicy(
        max_attempts=max_attempts,
        base_s=base_s,
        max_s=max_s,
        jitter_ratio=jitter_ratio,
        retry_on=(Exception,),
        give_up_on=NON_RETRYABLE_PROVIDER_ERRORS + (asyncio.CancelledError,),
    )


class ProviderRetryContext:
    """一次带重试的 Provider 调用上下文（记录尝试次数与耗时）。

    Attributes:
        provider: Provider 注册名。
        model: 模型名。
        attempts: 实际尝试次数。
        last_error: 最后一次错误。
    """

    __slots__ = ("provider", "model", "attempts", "last_error")

    def __init__(self, *, provider: str, model: str = "") -> None:
        """初始化上下文。"""
        self.provider = provider
        self.model = model
        self.attempts: int = 0
        self.last_error: BaseException | None = None


async def call_with_retry(
    func: Callable[[], Awaitable[T]],
    *,
    provider: str,
    model: str = "",
    max_attempts: int = 4,
    base_s: float = 1.0,
    max_s: float = 30.0,
    jitter_ratio: float = 0.2,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    rng: Any = None,
) -> T:
    """带指数退避与重试的 Provider 调用入口。

    语义：
    * 可重试错误（限流 / 超时 / 服务端错误）按退避策略重试；
    * 不可重试错误（鉴权 / 模型不支持 / 内容拦截 / 全部不可用）**立即抛出**；
    * 非 ``LoopForgeError`` 异常立即抛出（不掩盖 bug）；
    * 最后一次失败原样抛出。

    Args:
        func: 无参异步调用（闭包捕获 provider.chat 等）。
        provider: Provider 注册名（日志与错误上下文）。
        model: 模型名。
        max_attempts: 总尝试次数（含首次）。
        base_s: 基础退避秒数。
        max_s: 退避上限秒数。
        jitter_ratio: 抖动比例。
        on_retry: 每次重试前的回调 ``(attempt, exc, delay_s) -> None``。
        rng: 随机源，测试可注入。

    Returns:
        ``func`` 的返回值。

    Raises:
        ProviderError: 最终失败时原样抛出。
    """
    policy = provider_retry_policy(
        max_attempts=max_attempts,
        base_s=base_s,
        max_s=max_s,
        jitter_ratio=jitter_ratio,
    )
    context = ProviderRetryContext(provider=provider, model=model)

    async def _attempt() -> T:
        context.attempts += 1
        try:
            return await func()
        except BaseException as exc:  # noqa: BLE001 —— 统一记录后按策略决定
            context.last_error = exc
            raise

    decorated = retry_async(
        max_attempts=policy.max_attempts,
        base_s=policy.base_s,
        max_s=policy.max_s,
        jitter_ratio=policy.jitter_ratio,
        retry_on=policy.retry_on,
        give_up_on=policy.give_up_on,
        on_retry=on_retry,
        rng=rng,
    )(_attempt)
    return await decorated()
