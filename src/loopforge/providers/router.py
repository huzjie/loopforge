"""Provider 路由与故障转移 —— 按角色 / 能力 / 成本选择 Provider 并编排失败切换。

出口签名（``TASK_BREAKDOWN`` §11.3 冻结）::

    class ProviderRouter:
        async def resolve(self, role: RoleName, hint: RoutingHint | None = None) -> ProviderBinding: ...

路由优先级（``ARCHITECTURE`` §7.1）
----------------------------------
1. **hint 显式指定**（``RoutingHint.provider`` 非空且可用）；
2. **DB 覆盖**（``agent_roles`` 已配置该角色 → 用 DB 绑定，需传入 ``uow``）；
3. **settings 绑定**（``settings.roles.for_role(role)``）；
4. **默认**（``settings.providers.default`` + 模型）。

``ProviderBinding`` 携带已实例化的 Provider 与故障转移候选
（``fallbacks`` 有序元组），供 FailoverChain 使用。

FailoverChain
-------------
* 按 ``binding.fallbacks`` 顺序尝试，主 Provider 失败时切到备；
* 用 :class:`~loopforge.core.resilience.CircuitBreaker` 做熔断冷却
  （连续失败超过阈值后跳过该 Provider 一段时间）；
* 记录每次切换原因（``switch_reasons``），供审计与 WP5 治理使用；
* 全部失败抛 :class:`AllProvidersUnavailableError`（``LF-PROVIDER-0705``）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

from loopforge.core.clock import Clock, get_clock
from loopforge.core.enums import RoleName
from loopforge.core.errors import (
    AllProvidersUnavailableError,
    CircuitOpenError,
    ProviderError,
)
from loopforge.core.logging import get_logger
from loopforge.core.resilience import CircuitBreaker
from loopforge.providers.registry import create_provider, get_provider_class
from loopforge.providers.types import (
    ProviderBinding,
    ProviderConfigModel,
    RoutingHint,
)

__all__ = [
    "ProviderRouter",
    "FailoverChain",
    "SwitchRecord",
    "DEFAULT_FAILOVER_CHAIN",
]

_LOG = get_logger(__name__)

#: 全局熔断器命名前缀。
_BREAKER_PREFIX: Final[str] = "provider:"

#: 默认故障转移链（settings 未配置时的兜底）。
DEFAULT_FAILOVER_CHAIN: Final[tuple[str, ...]] = ("mock",)


@dataclass(slots=True)
class SwitchRecord:
    """一次故障转移的审计记录。

    Attributes:
        from_provider: 失败的主 Provider。
        to_provider: 切换到的备 Provider。
        reason: 失败原因（错误码或异常类型名）。
        attempt: 这是第几次尝试（1-based）。
    """

    from_provider: str
    to_provider: str
    reason: str
    attempt: int = 1


def _matches_hint(
    *,
    provider_name: str,
    provider: Any,
    hint: RoutingHint | None,
    model: str,
) -> bool:
    """判断 Provider + 模型是否满足 hint 约束。

    Args:
        provider_name: Provider 名。
        provider: Provider 实例。
        hint: 路由提示。
        model: 模型名。

    Returns:
        满足返回 ``True``。
    """
    if hint is None:
        return True
    if hint.provider and hint.provider.lower() != provider_name.lower():
        return False
    if hint.exclude and provider_name.lower() in {e.lower() for e in hint.exclude}:
        return False
    caps = provider.capabilities
    if hint.require_tools and not caps.supports_tools:
        return False
    if hint.require_streaming and not caps.supports_streaming:
        return False
    if hint.require_json_mode and not caps.supports_json_mode:
        return False
    if hint.min_context_window and caps.context_window < hint.min_context_window:
        return False
    return True


class ProviderRouter:
    """按角色解析 Provider 绑定。

    Attributes:
        clock: 时间源。
        breakers: Provider 名 → 熔断器（跨 resolve 复用）。
        db_enabled: 是否启用 DB 覆盖（resolve 时传 ``uow`` 才生效）。
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        breaker_failure_threshold: int = 5,
        breaker_recovery_timeout_s: float = 60.0,
        breaker_half_open_max_calls: int = 2,
    ) -> None:
        """初始化路由器。

        Args:
            clock: 时间源。
            breaker_failure_threshold: 熔断阈值。
            breaker_recovery_timeout_s: 熔断恢复时长。
            breaker_half_open_max_calls: 半开态成功阈值。
        """
        self.clock: Clock = clock or get_clock()
        self.breakers: dict[str, CircuitBreaker] = {}
        self._breaker_threshold = breaker_failure_threshold
        self._breaker_timeout = breaker_recovery_timeout_s
        self._breaker_half_open = breaker_half_open_max_calls

    def _breaker_for(self, provider_name: str) -> CircuitBreaker:
        """取（必要时创建）某 Provider 的熔断器。

        Args:
            provider_name: Provider 名。

        Returns:
            熔断器实例。
        """
        breaker = self.breakers.get(provider_name)
        if breaker is None:
            breaker = CircuitBreaker(
                name=f"{_BREAKER_PREFIX}{provider_name}",
                failure_threshold=self._breaker_threshold,
                recovery_timeout_s=self._breaker_timeout,
                half_open_max_calls=self._breaker_half_open,
                clock=self.clock,
            )
            self.breakers[provider_name] = breaker
        return breaker

    # ------------------------------------------------------------------ 解析

    def _settings_binding(self, role: RoleName) -> ProviderConfigModel:
        """从 ``settings.roles`` 构造 Provider 配置（DB 未覆盖时使用）。

        Args:
            role: 角色。

        Returns:
            Provider 配置。
        """
        from loopforge.core.config import get_settings

        settings = get_settings()
        binding = settings.roles.for_role(role.value)
        return ProviderConfigModel(
            name=binding.provider,
            kind=binding.provider,
            default_model=binding.model,
            enabled=True,
        )

    async def _db_binding(
        self, role: RoleName, uow: Any
    ) -> ProviderConfigModel | None:
        """从 DB ``agent_roles`` / ``provider_configs`` 构造绑定。

        Args:
            role: 角色。
            uow: 工作单元（只读）。

        Returns:
            Provider 配置；角色未配置返回 ``None``。
        """
        try:
            role_row = await uow.agent_roles.get_by_name(role)
        except Exception:  # noqa: BLE001 —— DB 未初始化等场景静默回落 settings
            return None
        if role_row is None or not role_row.enabled:
            return None
        provider_cfg = role_row.provider_config
        if provider_cfg is None or not provider_cfg.enabled:
            return None
        api_key = None
        try:
            api_key = _decrypt_provider_key(provider_cfg)
        except Exception:  # noqa: BLE001 —— 解密失败不阻塞路由
            _LOG.warning(
                "provider_key_decrypt_failed",
                provider=provider_cfg.name,
                role=role.value,
            )
        return ProviderConfigModel.from_orm_config(
            provider_cfg, api_key=api_key
        ).model_copy(
            update={
                "default_model": role_row.model or provider_cfg.default_model,
                "options": {
                    **dict(provider_cfg.options or {}),
                    "temperature": role_row.temperature,
                    "max_tokens": role_row.max_tokens,
                },
            }
        )

    async def resolve(
        self, role: RoleName, hint: RoutingHint | None = None, *, uow: Any = None
    ) -> ProviderBinding:
        """解析角色到 Provider 绑定。

        Args:
            role: 角色。
            hint: 路由提示。
            uow: 可选的 DB 工作单元（启用 DB 覆盖时传入）。

        Returns:
            绑定（含已实例化 Provider 与 fallback 链）。

        Raises:
            ProviderError: 配置的 Provider 未注册。
        """
        config: ProviderConfigModel | None = None
        source: str = "default"
        if uow is not None:
            db_config = await self._db_binding(role, uow)
            if db_config is not None:
                config = db_config
                source = "database"
        if config is None:
            config = self._settings_binding(role)
            source = "settings"

        provider_name = config.name
        # hint 显式指定时覆盖。
        if hint is not None and hint.provider:
            provider_name = hint.provider
            config = ProviderConfigModel(
                name=provider_name,
                kind=provider_name,
                default_model=hint.model or config.default_model,
                enabled=True,
            )
            source = "hint"

        provider = create_provider(config, clock=self.clock)
        if not _matches_hint(
            provider_name=provider_name,
            provider=provider,
            hint=hint,
            model=config.default_model or "",
        ):
            raise ProviderError(
                f"Provider {provider_name} 不满足路由提示",
                details={"provider": provider_name, "role": role.value},
            )

        model = config.default_model or provider.default_model
        fallbacks = self._build_fallbacks(
            provider_name, model, config, hint=hint
        )
        temperature = float(config.option("temperature", 0.2) or 0.2)
        max_tokens = int(config.option("max_tokens", 4096) or 4096)
        return ProviderBinding(
            role=role,
            provider_name=provider_name,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            provider=provider,
            fallbacks=fallbacks,
            source=source,  # type: ignore[arg-type]
        )

    def _build_fallbacks(
        self,
        provider_name: str,
        model: str,
        config: ProviderConfigModel,
        *,
        hint: RoutingHint | None,
    ) -> tuple[tuple[str, str], ...]:
        """构造故障转移候选链（有序、去重、不含主 Provider）。

        Args:
            provider_name: 主 Provider 名。
            model: 主模型。
            config: 主配置。
            hint: 路由提示。

        Returns:
            候选 ``(provider_name, model)`` 元组序列。
        """
        candidates: list[tuple[str, str]] = []
        seen: set[str] = {provider_name}

        def add(name: str, mdl: str) -> None:
            key = f"{name}:{mdl}"
            if name in seen or key in {f"{n}:{m}" for n, m in candidates}:
                return
            candidates.append((name, mdl))

        # 1. config.failover_to（DB / 配置显式指定）。
        if config.failover_to:
            add(config.failover_to, "")
        # 2. settings.failover_chain。
        try:
            from loopforge.core.config import get_settings

            chain = get_settings().providers.failover_chain
            if isinstance(chain, list):
                for name in chain:
                    add(str(name), "")
        except Exception:  # noqa: BLE001 —— 配置不可用时用默认链
            for name in DEFAULT_FAILOVER_CHAIN:
                add(name, "")
        # 3. hint.exclude 过滤已在 _matches_hint 处理主链；这里跳过被排除项。
        excluded = {e.lower() for e in (hint.exclude if hint else [])}
        result: list[tuple[str, str]] = []
        for name, mdl in candidates:
            if name.lower() in excluded:
                continue
            resolved_model = mdl or model
            try:
                cls = get_provider_class(name)
                default_model = getattr(cls, "default_model", "")
                resolved_model = resolved_model or default_model or "mock-large"
            except Exception:  # noqa: BLE001 —— 未注册的候选跳过
                continue
            result.append((name, resolved_model))
        return tuple(result)

    # ------------------------------------------------------------------ 便捷

    async def binding_for_role(self, role: RoleName, uow: Any = None) -> ProviderBinding:
        """等价于 :meth:`resolve`（无 hint）。"""
        return await self.resolve(role, None, uow=uow)


class FailoverChain:
    """主备失败切换执行器。

    用法::

        chain = FailoverChain(router.breakers, clock=router.clock)
        result = await chain.run(
            binding,
            call=lambda provider, model: provider.chat(request),
        )

    失败切换规则：
    * 仅对 :class:`~loopforge.core.errors.ProviderError` 触发切换；
    * ``retryable=False`` 的错误（鉴权 / 模型不支持）**不切换**，直接抛出；
    * 熔断器 OPEN 的 Provider 直接跳过；
    * 每次切换记录一条 :class:`SwitchRecord`。
    """

    def __init__(
        self,
        breakers: dict[str, CircuitBreaker] | None = None,
        *,
        clock: Clock | None = None,
        max_fallback_hops: int = 6,
    ) -> None:
        """初始化。

        Args:
            breakers: 共享熔断器表（通常来自 ``ProviderRouter.breakers``）。
            clock: 时间源。
            max_fallback_hops: 最多尝试多少个 Provider。
        """
        self.breakers: dict[str, CircuitBreaker] = breakers or {}
        self.clock: Clock = clock or get_clock()
        self.max_fallback_hops = max_fallback_hops
        self.switch_reasons: list[SwitchRecord] = []

    def _breaker(self, provider_name: str) -> CircuitBreaker | None:
        """取某 Provider 的熔断器。"""
        return self.breakers.get(provider_name)

    async def run(
        self,
        binding: ProviderBinding,
        call: Callable[[Any, str], Awaitable[Any]],
    ) -> Any:
        """沿 fallback 链执行调用。

        Args:
            binding: 路由绑定（主 Provider + fallbacks）。
            call: ``async (provider, model) -> result`` 的调用闭包。

        Returns:
            第一次成功的结果。

        Raises:
            AllProvidersUnavailableError: 全部 Provider 失败（``LF-PROVIDER-0705``）。
        """
        self.switch_reasons.clear()
        provider = binding.require_provider()
        candidates: list[tuple[Any, str, str]] = [
            (provider, binding.provider_name, binding.model)
        ]
        for name, model in binding.fallbacks:
            try:
                fallback = create_provider(
                    ProviderConfigModel(name=name, kind=name, default_model=model),
                    clock=self.clock,
                )
            except ProviderError:
                continue
            candidates.append((fallback, name, model))

        last_error: ProviderError | None = None
        for attempt, (candidate, name, model) in enumerate(
            candidates[: self.max_fallback_hops], start=1
        ):
            breaker = self._breaker(name)
            if breaker is not None and not await breaker.allow():
                self.switch_reasons.append(
                    SwitchRecord(
                        from_provider=name,
                        to_provider=(
                            candidates[attempt][1]
                            if attempt < len(candidates)
                            else ""
                        ),
                        reason="circuit_open",
                        attempt=attempt,
                    )
                )
                continue
            try:
                result = await call(candidate, model)
            except ProviderError as exc:
                last_error = exc
                if breaker is not None:
                    await breaker.record_failure()
                if not exc.retryable:
                    raise
                if attempt < len(candidates):
                    self.switch_reasons.append(
                        SwitchRecord(
                            from_provider=name,
                            to_provider=candidates[attempt][1],
                            reason=exc.code,
                            attempt=attempt,
                        )
                    )
                continue
            except CircuitOpenError as exc:
                last_error = exc
                continue
            if breaker is not None:
                await breaker.record_success()
            return result

        raise AllProvidersUnavailableError(
            "全部 Provider 不可用",
            details={
                "role": binding.role.value,
                "primary": binding.provider_name,
                "fallbacks": list(binding.fallbacks),
                "last_error": last_error.code if last_error else None,
                "switches": [
                    f"{r.from_provider}->{r.to_provider}:{r.reason}"
                    for r in self.switch_reasons
                ],
            },
        )


def _decrypt_provider_key(provider_cfg: Any) -> str | None:
    """解密 Provider 的 API Key（WP7 安全层集成点）。

    默认实现：读取 ``provider_cfg.api_key_enc``，用 ``core.security`` 解密；
    未配置密钥时返回 ``None``。

    Args:
        provider_cfg: ORM ``ProviderConfig`` 实例。

    Returns:
        明文 Key；无密文返回 ``None``。
    """
    enc = getattr(provider_cfg, "api_key_enc", None)
    if not enc:
        return None
    try:
        from loopforge.core.security import decrypt_secret

        return decrypt_secret(str(enc))
    except ImportError:
        # WP1 未交付 security 模块前的临时回退：无解密能力时视为无 Key。
        return None
