"""``BaseProvider`` 抽象基类 —— **跨包冻结契约**。

签名逐字对齐 ``docs/ARCHITECTURE.md`` §7.1 与 ``docs/TASK_BREAKDOWN.md`` §11.3::

    class BaseProvider(abc.ABC):
        name: ClassVar[str]

        def __init__(self, config: ProviderConfigModel, clock: Clock) -> None: ...

        @property
        @abc.abstractmethod
        def capabilities(self) -> ProviderCapabilities: ...

        @abc.abstractmethod
        async def chat(self, request: ChatRequest) -> ChatResult: ...

        @abc.abstractmethod
        async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]: ...

        @abc.abstractmethod
        def count_tokens(self, messages: Sequence[Message], model: str) -> int: ...

        @abc.abstractmethod
        async def health_check(self) -> ProviderHealth: ...

        async def close(self) -> None: ...

``chat_stream`` 的实现形态
--------------------------
声明为 ``async def ... -> AsyncIterator[StreamChunk]`` 且函数体内 ``yield``，
即 **异步生成器函数**。调用方写法固定为::

    async for chunk in provider.chat_stream(request):
        ...

**不要** ``await provider.chat_stream(...)``。

铁律
----
1. **Provider 层不做重试**（``TASK_BREAKDOWN`` §4.4-5）。重试与熔断统一由
   :mod:`loopforge.core.resilience` 与 :mod:`loopforge.providers.failover` 编排，
   避免双层重试把成本放大成平方级。
2. **异常必须归一**：任何外部报文都要落到
   ``LF-PROVIDER-0701 ~ 0709`` 之一，上层只认错误码。
3. **时间只走 Clock**，禁止 ``datetime.now()`` / ``time.time()``；
   延迟测量用 ``clock.monotonic()``。
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Mapping, Sequence
from decimal import Decimal
from types import TracebackType
from typing import Any, ClassVar, Final, Self

import httpx

from loopforge.core.clock import Clock, get_clock
from loopforge.core.error_codes import ErrorCode
from loopforge.core.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
)
from loopforge.core.logging import get_logger
from loopforge.providers.types import (
    ChatRequest,
    ChatResult,
    EmbeddingRequest,
    EmbeddingResult,
    Message,
    ProviderCapabilities,
    ProviderConfigModel,
    ProviderHealth,
    StreamChunk,
    Usage,
)

__all__ = [
    "BaseProvider",
    "HttpProviderBase",
    "ProviderBadResponseError",
    "ProviderModelUnsupportedError",
    "ProviderContentFilteredError",
    "map_http_status",
    "map_transport_error",
    "DEFAULT_USER_AGENT",
]

_LOG = get_logger(__name__)

#: 统一 User-Agent，方便对端与网关识别流量来源。
DEFAULT_USER_AGENT: Final[str] = "LoopForge/0.1 (+https://github.com/loopforge/loopforge)"

#: 报文摘要写日志时的最大长度（防止把整段 prompt 泄进日志）。
_ERROR_EXCERPT_LIMIT: Final[int] = 400


class ProviderBadResponseError(ProviderError):
    """Provider 响应无法解析（``LF-PROVIDER-0708``）。"""

    default_code = ErrorCode.PROVIDER_BAD_RESPONSE


class ProviderModelUnsupportedError(ProviderError):
    """Provider 不支持该模型（``LF-PROVIDER-0706``）。"""

    default_code = ErrorCode.PROVIDER_MODEL_UNSUPPORTED


class ProviderContentFilteredError(ProviderError):
    """内容被 Provider 安全策略拦截（``LF-PROVIDER-0709``）。"""

    default_code = ErrorCode.PROVIDER_CONTENT_FILTERED


def _excerpt(text: str, limit: int = _ERROR_EXCERPT_LIMIT) -> str:
    """截断报文摘要，避免超长错误消息淹没日志。

    Args:
        text: 原始文本。
        limit: 最大长度。

    Returns:
        截断后的文本（超长时带省略号）。
    """
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


def map_http_status(
    status_code: int,
    *,
    provider: str,
    model: str = "",
    body: str = "",
    headers: Mapping[str, str] | None = None,
) -> ProviderError:
    """把 HTTP 状态码归一成 LoopForge Provider 异常。

    映射表（``TASK_BREAKDOWN`` §4.4-3）：

    ============  ==================================  ==========
    状态码         异常                                 retryable
    ============  ==================================  ==========
    401 / 403     :class:`ProviderAuthError`           否
    404           :class:`ProviderModelUnsupportedError` 否
    408           :class:`ProviderTimeoutError`        是
    429           :class:`ProviderRateLimitError`      是
    4xx 其余       :class:`ProviderBadResponseError`    是
    5xx           :class:`ProviderServerError`         是
    ============  ==================================  ==========

    Args:
        status_code: HTTP 状态码。
        provider: Provider 注册名。
        model: 模型名。
        body: 响应体文本（会被截断）。
        headers: 响应头（用于提取 ``Retry-After``）。

    Returns:
        构造好的异常实例（**返回而非抛出**，由调用方决定抛出时机）。
    """
    details: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "http_status": status_code,
        "body_excerpt": _excerpt(body),
    }
    retry_after = (headers or {}).get("retry-after") or (headers or {}).get("Retry-After")
    if retry_after:
        details["retry_after_s"] = str(retry_after)

    message = f"{provider} 返回 HTTP {status_code}"
    if status_code in (401, 403):
        return ProviderAuthError(f"{message}：鉴权失败", details=details)
    if status_code == 404:
        return ProviderModelUnsupportedError(f"{message}：模型或端点不存在", details=details)
    if status_code == 408:
        return ProviderTimeoutError(f"{message}：请求超时", details=details)
    if status_code == 429:
        return ProviderRateLimitError(f"{message}：触发限流", details=details)
    if 400 <= status_code < 500:
        return ProviderBadResponseError(f"{message}：请求被拒绝", details=details)
    return ProviderServerError(f"{message}：服务端错误", details=details)


def map_transport_error(
    exc: Exception, *, provider: str, model: str = "", timeout_s: float = 0.0
) -> ProviderError:
    """把 ``httpx`` 传输层异常归一成 LoopForge Provider 异常。

    Args:
        exc: 原始异常。
        provider: Provider 注册名。
        model: 模型名。
        timeout_s: 本次请求配置的超时秒数。

    Returns:
        归一后的异常实例。
    """
    details: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "cause_type": type(exc).__name__,
        "cause": _excerpt(str(exc)),
    }
    if timeout_s:
        details["timeout_s"] = timeout_s
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeoutError(f"{provider} 请求超时（{timeout_s}s）", details=details)
    if isinstance(exc, httpx.TransportError):
        return ProviderServerError(f"{provider} 网络传输失败", details=details)
    return ProviderServerError(f"{provider} 调用失败", details=details)


class BaseProvider(abc.ABC):
    """所有模型供应商的抽象基类。

    子类**必须**声明类属性 ``name``（注册名）；``aliases`` 可选。
    :mod:`loopforge.providers.registry` 会扫描本类的具体子类并自动注册，
    因此新增 Provider **零公共文件改动**（``TASK_BREAKDOWN`` §13.3）。

    Attributes:
        name: 注册名，全局唯一，小写。
        aliases: 别名元组。
        kind: 对应 ORM ``provider_configs.kind`` 的取值。
        default_base_url: 未配置 ``base_url`` 时使用的默认端点。
        default_model: 未指定模型时的兜底模型名。
        free: 是否零成本（本地推理与 Mock 为 ``True``）。
    """

    name: ClassVar[str] = ""
    aliases: ClassVar[tuple[str, ...]] = ()
    kind: ClassVar[str] = "openai_compatible"
    default_base_url: ClassVar[str] = ""
    default_model: ClassVar[str] = ""
    free: ClassVar[bool] = False

    def __init__(self, config: ProviderConfigModel, clock: Clock) -> None:
        """初始化 Provider。

        Args:
            config: 运行期配置（内存投影，不含密文）。
            clock: 时间源；**唯一合法的「现在」来源**。
        """
        self.config: ProviderConfigModel = config
        self.clock: Clock = clock
        self._closed: bool = False

    # ------------------------------------------------------------------ 抽象

    @property
    @abc.abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """声明本 Provider 的能力，路由器据此过滤候选。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResult:
        """非流式对话补全。

        Args:
            request: 对话请求。

        Returns:
            完整结果。

        Raises:
            ProviderError: 任何失败都归一为 ``LF-PROVIDER-07xx``。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        """流式对话补全（异步生成器）。

        Args:
            request: 对话请求。

        Yields:
            :class:`StreamChunk` 增量片段，最后一个带 ``finish_reason``。

        Raises:
            ProviderError: 任何失败都归一为 ``LF-PROVIDER-07xx``。
        """
        raise NotImplementedError
        yield StreamChunk()  # pragma: no cover —— 使本函数成为异步生成器

    @abc.abstractmethod
    def count_tokens(self, messages: Sequence[Message], model: str) -> int:
        """估算一组消息的 prompt token 数（**同步**，不得发网络请求）。

        Args:
            messages: 消息序列。
            model: 模型名。

        Returns:
            token 估算值。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def health_check(self) -> ProviderHealth:
        """探活。实现应尽量廉价（列模型 / 极小请求），且**不得抛异常**。

        Returns:
            健康状态。
        """
        raise NotImplementedError

    # ------------------------------------------------------------------ 默认实现

    async def close(self) -> None:
        """释放底层资源（连接池等）。允许重复调用。"""
        self._closed = True

    @property
    def closed(self) -> bool:
        """是否已关闭。"""
        return self._closed

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """文本向量化 —— **可选能力**，默认不支持。

        Args:
            request: 向量化请求。

        Returns:
            向量化结果。

        Raises:
            ProviderModelUnsupportedError: 本 Provider 未实现向量化。
        """
        raise ProviderModelUnsupportedError(
            f"{self.name} 不支持 embedding",
            details={"provider": self.name, "model": request.model},
        )

    def resolve_model(self, model: str | None = None) -> str:
        """按「入参 → 配置 default_model → 类默认」的顺序确定模型名。

        Args:
            model: 调用方指定的模型名。

        Returns:
            最终模型名。

        Raises:
            ProviderModelUnsupportedError: 三层都取不到模型名。
        """
        for candidate in (model, self.config.default_model, self.default_model):
            if candidate and str(candidate).strip():
                return str(candidate).strip()
        raise ProviderModelUnsupportedError(
            f"{self.name} 未指定模型名", details={"provider": self.name}
        )

    def base_url(self) -> str:
        """返回去掉尾部斜杠的端点地址。

        Returns:
            端点 URL。
        """
        raw = self.config.base_url or self.default_base_url
        return raw.rstrip("/")

    def estimate_cost(self, request: ChatRequest, *, completion_tokens: int = 0) -> Decimal:
        """调用前的成本预估（供 WP5 BudgetGuard 预检）。

        Args:
            request: 对话请求。
            completion_tokens: 预期输出 token；``0`` 时按 ``max_tokens`` 的
                四分之一做保守估计。

        Returns:
            预估花费（USD，8 位小数）。
        """
        from loopforge.providers.pricing import estimate_cost as _estimate

        model = self.resolve_model(request.model)
        prompt_tokens = self.count_tokens(request.messages, model)
        if completion_tokens <= 0:
            ceiling = request.max_tokens or self.capabilities.max_output_tokens
            completion_tokens = max(1, ceiling // 4)
        return _estimate(
            self.name,
            model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def actual_cost(self, model: str, usage: Usage) -> Decimal:
        """调用后的实际成本（据真实 usage 回填）。

        Args:
            model: 模型名。
            usage: 真实用量。

        Returns:
            实际花费（USD，8 位小数）。
        """
        from loopforge.providers.pricing import actual_cost as _actual

        return _actual(self.name, model, usage)

    def supports(
        self, *, tools: bool = False, streaming: bool = False, json_mode: bool = False
    ) -> bool:
        """一次性检查多项能力。

        Args:
            tools: 是否要求工具调用。
            streaming: 是否要求流式。
            json_mode: 是否要求 JSON 模式。

        Returns:
            全部满足返回 ``True``。
        """
        caps = self.capabilities
        if tools and not caps.supports_tools:
            return False
        if streaming and not caps.supports_streaming:
            return False
        return not (json_mode and not caps.supports_json_mode)

    def elapsed_ms(self, started: float) -> int:
        """基于 ``clock.monotonic()`` 计算耗时毫秒。

        Args:
            started: 起始的单调时间戳。

        Returns:
            非负整数毫秒。
        """
        return max(0, int((self.clock.monotonic() - started) * 1000))

    async def __aenter__(self) -> Self:
        """支持 ``async with provider:``。"""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """退出时自动 :meth:`close`。"""
        await self.close()
        return False

    def __repr__(self) -> str:
        """调试友好表示（**绝不包含 api_key**）。"""
        return (
            f"{type(self).__name__}(name={self.name!r}, "
            f"config={self.config.name!r}, model={self.config.default_model!r})"
        )


class HttpProviderBase(BaseProvider):
    """基于 ``httpx.AsyncClient`` 的 Provider 公共骨架。

    统一处理：连接池生命周期、超时、请求头、状态码归一、测试打桩。

    测试打桩方式（**不改动冻结的 ``__init__`` 签名**）::

        cfg = ProviderConfigModel(
            name="openai",
            options={"transport": httpx.MockTransport(handler)},
        )
        provider = OpenAICompatProvider(cfg, FrozenClock())
    """

    def __init__(self, config: ProviderConfigModel, clock: Clock) -> None:
        """初始化并延迟创建 HTTP 客户端。"""
        super().__init__(config, clock)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ 客户端

    def default_headers(self) -> dict[str, str]:
        """构造默认请求头。子类覆盖以追加鉴权头。

        Returns:
            请求头字典。
        """
        headers: dict[str, str] = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        extra = self.config.option("headers")
        if isinstance(extra, Mapping):
            headers.update({str(k): str(v) for k, v in extra.items()})
        return headers

    def client(self) -> httpx.AsyncClient:
        """返回（必要时创建）共享的异步 HTTP 客户端。

        Returns:
            ``httpx.AsyncClient`` 实例。

        Raises:
            ProviderError: Provider 已关闭。
        """
        if self._closed:
            raise ProviderError(
                f"{self.name} 已关闭，无法继续发起请求", details={"provider": self.name}
            )
        if self._client is None:
            timeout = self.config.float_option("request_timeout_s", 120.0)
            transport = self.config.option("transport")
            kwargs: dict[str, Any] = {
                "base_url": self.base_url(),
                "headers": self.default_headers(),
                "timeout": httpx.Timeout(timeout, connect=min(10.0, timeout)),
                "follow_redirects": True,
            }
            if isinstance(transport, httpx.AsyncBaseTransport):
                kwargs["transport"] = transport
            proxy = self.config.option("proxy")
            if isinstance(proxy, str) and proxy:
                kwargs["proxy"] = proxy
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def close(self) -> None:
        """关闭连接池。允许重复调用。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        await super().close()

    # ------------------------------------------------------------------ 请求

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        model: str = "",
        timeout_s: float | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """发一次 JSON 请求并归一化异常。

        Args:
            method: HTTP 方法。
            path: 相对路径（相对 ``base_url``）。
            json_body: 请求体。
            model: 模型名（仅用于错误上下文）。
            timeout_s: 覆盖超时。
            headers: 追加请求头。

        Returns:
            解析后的 JSON 字典。

        Raises:
            ProviderError: 状态码非 2xx、传输失败或响应体不是 JSON 对象。
        """
        client = self.client()
        try:
            response = await client.request(
                method,
                path,
                json=dict(json_body) if json_body is not None else None,
                headers=dict(headers) if headers else None,
                timeout=timeout_s if timeout_s is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except Exception as exc:  # noqa: BLE001 —— 统一归一为 ProviderError
            raise map_transport_error(
                exc, provider=self.name, model=model, timeout_s=timeout_s or 0.0
            ) from exc

        if response.status_code >= 400:
            raise map_http_status(
                response.status_code,
                provider=self.name,
                model=model,
                body=response.text,
                headers=dict(response.headers),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(
                f"{self.name} 返回的不是合法 JSON",
                details={"provider": self.name, "model": model, "body_excerpt": _excerpt(response.text)},
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderBadResponseError(
                f"{self.name} 返回的 JSON 顶层不是对象",
                details={"provider": self.name, "model": model, "json_type": type(payload).__name__},
            )
        return payload

    async def probe(self, path: str, *, expect: int = 200) -> ProviderHealth:
        """通用探活实现：GET 某个廉价端点。

        Args:
            path: 相对路径。
            expect: 期望的状态码。

        Returns:
            健康状态（**不抛异常**）。
        """
        started = self.clock.monotonic()
        try:
            response = await self.client().get(path, timeout=10.0)
        except Exception as exc:  # noqa: BLE001 —— health_check 契约要求不抛
            mapped = map_transport_error(exc, provider=self.name)
            return ProviderHealth(
                name=self.name,
                healthy=False,
                latency_ms=self.elapsed_ms(started),
                checked_at=self.clock.now(),
                detail=_excerpt(str(exc)),
                model=self.config.default_model,
                error_code=mapped.code,
            )
        healthy = response.status_code == expect
        error_code: str | None = None
        if not healthy:
            error_code = map_http_status(
                response.status_code, provider=self.name, body=response.text
            ).code
        return ProviderHealth(
            name=self.name,
            healthy=healthy,
            latency_ms=self.elapsed_ms(started),
            checked_at=self.clock.now(),
            detail=f"HTTP {response.status_code}",
            model=self.config.default_model,
            error_code=error_code,
        )


def _default_clock() -> Clock:
    """模块内取默认时钟的唯一入口（便于静态扫描白名单）。

    Returns:
        进程级默认 :class:`Clock`。
    """
    return get_clock()
