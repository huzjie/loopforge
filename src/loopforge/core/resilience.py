"""韧性原语：重试、熔断、限流、超时。

提供四件套（``ARCHITECTURE.md`` §3.1.2）：

* :func:`retry_async` —— 指数退避 + 抖动的异步重试装饰器；
* :class:`CircuitBreaker` —— 三态熔断器（CLOSED / OPEN / HALF_OPEN）；
* :class:`TokenBucketLimiter` —— 令牌桶限流；
* :func:`timeout_guard` —— 带业务错误码的超时上下文管理器。

四者都**只依赖 :mod:`loopforge.core.clock`**，不直接调用 ``time.time()``，
因此可在 :class:`~loopforge.core.clock.FrozenClock` 下做确定性测试。
"""

from __future__ import annotations

import asyncio
import functools
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, ParamSpec, TypeVar

from loopforge.core.clock import Clock, get_clock
from loopforge.core.errors import CircuitOpenError, LoopForgeError, TimeoutError_

__all__ = [
    "BreakerState",
    "CircuitBreaker",
    "RetryPolicy",
    "TokenBucketLimiter",
    "retry_async",
    "timeout_guard",
    "compute_backoff",
]

P = ParamSpec("P")
T = TypeVar("T")

#: 默认抖动比例（0.2 表示退避时长在 ±20% 内随机）。
DEFAULT_JITTER_RATIO: Final[float] = 0.2


def compute_backoff(
    attempt: int,
    *,
    base_s: float = 1.0,
    max_s: float = 30.0,
    jitter_ratio: float = DEFAULT_JITTER_RATIO,
    rng: random.Random | None = None,
) -> float:
    """计算第 ``attempt`` 次重试前应等待的秒数（指数退避 + 抖动）。

    Args:
        attempt: 第几次重试，从 1 开始。
        base_s: 基础退避秒数。
        max_s: 退避上限秒数。
        jitter_ratio: 抖动比例，``0`` 表示不抖动。
        rng: 随机源；测试可注入固定种子的 :class:`random.Random`。

    Returns:
        应等待的秒数，恒 ``>= 0``。

    Raises:
        ValueError: 参数非法。
    """
    if attempt < 1:
        raise ValueError(f"attempt 必须 >= 1，收到 {attempt}")
    if base_s <= 0 or max_s <= 0:
        raise ValueError("base_s / max_s 必须为正数")
    if not 0.0 <= jitter_ratio < 1.0:
        raise ValueError(f"jitter_ratio 必须在 [0, 1) 内，收到 {jitter_ratio}")
    raw = min(base_s * (2 ** (attempt - 1)), max_s)
    if jitter_ratio == 0.0:
        return raw
    source = rng or random
    delta = raw * jitter_ratio
    return max(0.0, raw + source.uniform(-delta, delta))


@dataclass(slots=True)
class RetryPolicy:
    """重试策略配置。

    Attributes:
        max_attempts: 总尝试次数（含首次），``1`` 表示不重试。
        base_s: 基础退避秒数。
        max_s: 退避上限秒数。
        jitter_ratio: 抖动比例。
        retry_on: 触发重试的异常类型元组。
        give_up_on: 明确不重试的异常类型元组（优先级高于 ``retry_on``）。
    """

    max_attempts: int = 3
    base_s: float = 1.0
    max_s: float = 30.0
    jitter_ratio: float = DEFAULT_JITTER_RATIO
    retry_on: tuple[type[BaseException], ...] = (Exception,)
    give_up_on: tuple[type[BaseException], ...] = (asyncio.CancelledError,)

    def __post_init__(self) -> None:
        """校验配置合法性。

        Raises:
            ValueError: ``max_attempts`` < 1。
        """
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts 必须 >= 1，收到 {self.max_attempts}")

    def should_retry(self, exc: BaseException) -> bool:
        """判断某异常是否应触发重试。

        Args:
            exc: 捕获到的异常。

        Returns:
            应重试返回 ``True``。
        """
        if isinstance(exc, self.give_up_on):
            return False
        if isinstance(exc, LoopForgeError) and not exc.retryable:
            return False
        return isinstance(exc, self.retry_on)


def retry_async(
    *,
    max_attempts: int = 3,
    base_s: float = 1.0,
    max_s: float = 30.0,
    jitter_ratio: float = DEFAULT_JITTER_RATIO,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    give_up_on: tuple[type[BaseException], ...] = (asyncio.CancelledError,),
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    rng: random.Random | None = None,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """异步指数退避重试装饰器。

    语义要点：

    * :class:`~loopforge.core.errors.LoopForgeError` 的 ``retryable=False``
      会**直接放弃**，避免对 4xx 类错误做无意义重试；
    * ``asyncio.CancelledError`` 永不重试，保证优雅停机；
    * 最后一次失败原样抛出，不做包装，方便上层按类型分流。

    Args:
        max_attempts: 总尝试次数（含首次）。
        base_s: 基础退避秒数。
        max_s: 退避上限秒数。
        jitter_ratio: 抖动比例。
        retry_on: 触发重试的异常类型。
        give_up_on: 明确放弃的异常类型。
        on_retry: 每次重试前的回调 ``(attempt, exc, delay_s) -> None``。
        rng: 随机源，测试可注入。

    Returns:
        装饰器。

    Examples:
        >>> @retry_async(max_attempts=3, base_s=0.01, jitter_ratio=0.0)
        ... async def flaky() -> int:
        ...     return 42
    """
    policy = RetryPolicy(
        max_attempts=max_attempts,
        base_s=base_s,
        max_s=max_s,
        jitter_ratio=jitter_ratio,
        retry_on=retry_on,
        give_up_on=give_up_on,
    )

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_exc: BaseException | None = None
            for attempt in range(1, policy.max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 —— 由 policy 决定是否吞
                    last_exc = exc
                    if attempt >= policy.max_attempts or not policy.should_retry(exc):
                        raise
                    delay = compute_backoff(
                        attempt,
                        base_s=policy.base_s,
                        max_s=policy.max_s,
                        jitter_ratio=policy.jitter_ratio,
                        rng=rng,
                    )
                    if on_retry is not None:
                        on_retry(attempt, exc, delay)
                    await asyncio.sleep(delay)
            # 理论不可达：循环内要么 return 要么 raise。
            raise last_exc if last_exc is not None else RuntimeError("retry_async 内部状态异常")

        return wrapper

    return decorator


class BreakerState(StrEnum):
    """熔断器三态。"""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(slots=True)
class _BreakerStats:
    """熔断器内部计数。"""

    failures: int = 0
    successes: int = 0
    opened_at_monotonic: float = 0.0
    half_open_calls: int = 0


class CircuitBreaker:
    """三态熔断器 —— Provider 失败切换与工具保护的通用闸门。

    状态迁移::

        CLOSED --(连续失败 >= failure_threshold)--> OPEN
        OPEN   --(经过 recovery_timeout_s)-------> HALF_OPEN
        HALF_OPEN --(成功 >= half_open_max_calls)-> CLOSED
        HALF_OPEN --(任一失败)------------------> OPEN

    线程/协程安全：内部使用 :class:`asyncio.Lock`，同一事件循环内并发安全。
    """

    def __init__(
        self,
        *,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout_s: float = 60.0,
        half_open_max_calls: int = 2,
        clock: Clock | None = None,
    ) -> None:
        """初始化熔断器。

        Args:
            name: 熔断器名，用于日志与错误 details。
            failure_threshold: CLOSED 态连续失败多少次后跳闸。
            recovery_timeout_s: OPEN 态维持多久后允许试探。
            half_open_max_calls: HALF_OPEN 态需要连续成功多少次才闭合。
            clock: 时钟依赖，测试可注入 :class:`FrozenClock`。

        Raises:
            ValueError: 阈值参数非法。
        """
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold 必须 >= 1，收到 {failure_threshold}")
        if half_open_max_calls < 1:
            raise ValueError(f"half_open_max_calls 必须 >= 1，收到 {half_open_max_calls}")
        if recovery_timeout_s <= 0:
            raise ValueError(f"recovery_timeout_s 必须为正，收到 {recovery_timeout_s}")
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.half_open_max_calls = half_open_max_calls
        self._clock: Clock = clock or get_clock()
        self._state: BreakerState = BreakerState.CLOSED
        self._stats = _BreakerStats()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> BreakerState:
        """当前状态（不触发超时检查，只读快照）。"""
        return self._state

    @property
    def failure_count(self) -> int:
        """当前连续失败计数。"""
        return self._stats.failures

    def _maybe_half_open(self) -> None:
        """OPEN 态超时后切到 HALF_OPEN。"""
        if self._state is not BreakerState.OPEN:
            return
        elapsed = self._clock.monotonic() - self._stats.opened_at_monotonic
        if elapsed >= self.recovery_timeout_s:
            self._state = BreakerState.HALF_OPEN
            self._stats.half_open_calls = 0
            self._stats.successes = 0

    async def allow(self) -> bool:
        """询问当前是否放行一次调用。

        Returns:
            放行返回 ``True``；熔断中返回 ``False``。
        """
        async with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                return False
            if self._state is BreakerState.HALF_OPEN:
                if self._stats.half_open_calls >= self.half_open_max_calls:
                    return False
                self._stats.half_open_calls += 1
            return True

    async def record_success(self) -> None:
        """登记一次成功调用。"""
        async with self._lock:
            if self._state is BreakerState.HALF_OPEN:
                self._stats.successes += 1
                if self._stats.successes >= self.half_open_max_calls:
                    self._state = BreakerState.CLOSED
                    self._stats = _BreakerStats()
                return
            self._stats.failures = 0

    async def record_failure(self) -> None:
        """登记一次失败调用，必要时跳闸。"""
        async with self._lock:
            if self._state is BreakerState.HALF_OPEN:
                self._trip()
                return
            self._stats.failures += 1
            if self._stats.failures >= self.failure_threshold:
                self._trip()

    def _trip(self) -> None:
        """立即跳闸到 OPEN 态。"""
        self._state = BreakerState.OPEN
        self._stats.opened_at_monotonic = self._clock.monotonic()
        self._stats.half_open_calls = 0
        self._stats.successes = 0

    async def reset(self) -> None:
        """手工复位到 CLOSED（运维接口）。"""
        async with self._lock:
            self._state = BreakerState.CLOSED
            self._stats = _BreakerStats()

    async def call(self, func: Callable[[], Awaitable[T]]) -> T:
        """在熔断保护下执行一次异步调用。

        Args:
            func: 无参异步可调用对象。

        Returns:
            ``func`` 的返回值。

        Raises:
            CircuitOpenError: 熔断打开，调用被拒绝。
            BaseException: ``func`` 自身抛出的异常原样透传。
        """
        if not await self.allow():
            raise CircuitOpenError(
                f"熔断器 {self.name} 处于 {self._state} 状态，调用被拒绝",
                details={"breaker": self.name, "state": str(self._state)},
            )
        try:
            result = await func()
        except asyncio.CancelledError:
            raise
        except BaseException:
            await self.record_failure()
            raise
        await self.record_success()
        return result

    def __repr__(self) -> str:
        """调试友好表示。"""
        return f"CircuitBreaker(name={self.name!r}, state={self._state}, failures={self._stats.failures})"


@dataclass(slots=True)
class _BucketState:
    """令牌桶内部状态。"""

    tokens: float = 0.0
    last_refill_monotonic: float = field(default=0.0)


class TokenBucketLimiter:
    """令牌桶限流器 —— Provider QPS 与 API 速率限制共用。

    每秒按 ``rate`` 匀速补充令牌，桶容量 ``capacity`` 决定突发上限。
    """

    def __init__(
        self,
        *,
        rate: float,
        capacity: float | None = None,
        clock: Clock | None = None,
    ) -> None:
        """初始化令牌桶。

        Args:
            rate: 每秒补充的令牌数，必须为正。
            capacity: 桶容量；``None`` 时取 ``max(rate, 1.0)``。
            clock: 时钟依赖。

        Raises:
            ValueError: 参数非法。
        """
        if rate <= 0:
            raise ValueError(f"rate 必须为正，收到 {rate}")
        self.rate = rate
        self.capacity = capacity if capacity is not None else max(rate, 1.0)
        if self.capacity <= 0:
            raise ValueError(f"capacity 必须为正，收到 {self.capacity}")
        self._clock: Clock = clock or get_clock()
        self._state = _BucketState(
            tokens=self.capacity, last_refill_monotonic=self._clock.monotonic()
        )
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        """按流逝时间补充令牌。"""
        now = self._clock.monotonic()
        elapsed = max(0.0, now - self._state.last_refill_monotonic)
        self._state.tokens = min(self.capacity, self._state.tokens + elapsed * self.rate)
        self._state.last_refill_monotonic = now

    @property
    def available(self) -> float:
        """当前可用令牌数（只读快照，会先补充）。"""
        self._refill()
        return self._state.tokens

    async def try_acquire(self, tokens: float = 1.0) -> bool:
        """非阻塞尝试取令牌。

        Args:
            tokens: 需要的令牌数。

        Returns:
            成功返回 ``True``，不足返回 ``False``。

        Raises:
            ValueError: ``tokens`` 非正或超过桶容量。
        """
        if tokens <= 0:
            raise ValueError(f"tokens 必须为正，收到 {tokens}")
        if tokens > self.capacity:
            raise ValueError(f"tokens={tokens} 超过桶容量 {self.capacity}，永远无法满足")
        async with self._lock:
            self._refill()
            if self._state.tokens >= tokens:
                self._state.tokens -= tokens
                return True
            return False

    async def acquire(self, tokens: float = 1.0, *, timeout_s: float | None = None) -> None:
        """阻塞取令牌，直到成功或超时。

        Args:
            tokens: 需要的令牌数。
            timeout_s: 最长等待秒数；``None`` 表示无限等待。

        Raises:
            TimeoutError_: 超过 ``timeout_s`` 仍未取到令牌。
            ValueError: ``tokens`` 非法。
        """
        deadline = None if timeout_s is None else self._clock.monotonic() + timeout_s
        while True:
            if await self.try_acquire(tokens):
                return
            if deadline is not None and self._clock.monotonic() >= deadline:
                raise TimeoutError_(
                    f"限流等待超时（{timeout_s}s）",
                    details={"tokens": tokens, "rate": self.rate},
                )
            # 估算还需多久能攒够，避免忙等。
            async with self._lock:
                self._refill()
                deficit = max(0.0, tokens - self._state.tokens)
            await asyncio.sleep(min(max(deficit / self.rate, 0.001), 0.5))

    def __repr__(self) -> str:
        """调试友好表示。"""
        return f"TokenBucketLimiter(rate={self.rate}, capacity={self.capacity})"


@asynccontextmanager
async def timeout_guard(
    seconds: float,
    *,
    operation: str = "operation",
    details: dict[str, Any] | None = None,
) -> AsyncIterator[None]:
    """把 :class:`asyncio.TimeoutError` 转换成带错误码的业务异常。

    Args:
        seconds: 超时秒数，必须为正。
        operation: 操作名，写入错误 message 与 details。
        details: 附加上下文。

    Yields:
        无。

    Raises:
        TimeoutError_: 超时。
        ValueError: ``seconds`` 非正。

    Examples:
        >>> import asyncio
        >>> async def demo() -> str:
        ...     async with timeout_guard(1.0, operation="demo"):
        ...         return "ok"
        >>> asyncio.run(demo())
        'ok'
    """
    if seconds <= 0:
        raise ValueError(f"seconds 必须为正，收到 {seconds}")
    try:
        async with asyncio.timeout(seconds):
            yield
    except TimeoutError as exc:
        payload: dict[str, Any] = {"operation": operation, "timeout_s": seconds}
        if details:
            payload.update(details)
        raise TimeoutError_(f"{operation} 超时（{seconds}s）", details=payload) from exc


async def gather_with_concurrency(
    limit: int,
    awaitables: Sequence[Awaitable[T]],
    *,
    return_exceptions: bool = False,
) -> list[T | BaseException]:
    """带并发上限的 ``asyncio.gather``。

    Args:
        limit: 最大并发数，必须为正。
        awaitables: 待执行的可等待对象序列。
        return_exceptions: 与 ``asyncio.gather`` 同义。

    Returns:
        与入参同序的结果列表。

    Raises:
        ValueError: ``limit`` 非正。
    """
    if limit < 1:
        raise ValueError(f"limit 必须 >= 1，收到 {limit}")
    semaphore = asyncio.Semaphore(limit)

    async def _run(item: Awaitable[T]) -> T:
        async with semaphore:
            return await item

    return list(
        await asyncio.gather(
            *(_run(item) for item in awaitables), return_exceptions=return_exceptions
        )
    )
