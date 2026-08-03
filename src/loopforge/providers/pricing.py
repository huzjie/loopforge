"""模型价目表与成本换算 —— **金额一律 Decimal，禁止 float 参与计算**。

出口签名（``TASK_BREAKDOWN`` §11.3 冻结）::

    def estimate_cost(provider: str, model: str, *,
                      prompt_tokens: int, completion_tokens: int) -> Decimal: ...
    def actual_cost(provider: str, model: str, usage: Usage) -> Decimal: ...

「算两遍」原则（``TASK_BREAKDOWN`` §4.4-4）
-------------------------------------------
* :func:`estimate_cost` —— **调用前**，给 WP5 的 BudgetGuard 做预检；
* :func:`actual_cost` —— **调用后**，据真实 ``usage`` 回填账本。

缺任何一个，预算的 warn / throttle / halt 三级动作就会失效。

缓存计价
--------
``usage.cached_tokens`` 是 ``prompt_tokens`` 的子集，计价时拆成两段::

    (prompt_tokens - cached_tokens) * input_price + cached_tokens * cached_price
    + completion_tokens * output_price
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.error_codes import ErrorCode
from loopforge.core.errors import ConfigError, ProviderError
from loopforge.providers.types import Usage

__all__ = [
    "ModelPrice",
    "PricingTable",
    "ModelNotPricedError",
    "MONEY_QUANT",
    "TOKENS_PER_MILLION",
    "load_pricing",
    "get_pricing_table",
    "set_pricing_table",
    "reload_pricing",
    "price_of",
    "estimate_cost",
    "actual_cost",
    "quantize_money",
    "default_pricing_path",
]

#: 金额量化精度，与 ``constants.MONEY_SCALE`` 对齐（8 位小数）。
MONEY_QUANT: Final[Decimal] = Decimal("0.00000001")

#: 价目表以「每百万 token」为单位。
TOKENS_PER_MILLION: Final[Decimal] = Decimal(1_000_000)

_LOCK: Final[threading.Lock] = threading.Lock()
_TABLE: PricingTable | None = None


class ModelNotPricedError(ProviderError):
    """价目表里找不到该模型（``LF-PROVIDER-0706``）。"""

    default_code = ErrorCode.PROVIDER_MODEL_UNSUPPORTED


def quantize_money(value: Decimal) -> Decimal:
    """把金额量化到 8 位小数（四舍五入）。

    Args:
        value: 原始金额。

    Returns:
        量化后的 :class:`~decimal.Decimal`。
    """
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def default_pricing_path() -> Path:
    """返回仓库内置价目表路径。

    Returns:
        ``config/pricing.yaml`` 的绝对路径。
    """
    from loopforge.core.config import project_root

    return project_root() / "config" / "pricing.yaml"


class ModelPrice(BaseModel):
    """单个模型的价格（**已折算成 USD / 每百万 token**）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    input_per_mtok: Decimal = Decimal("0")
    output_per_mtok: Decimal = Decimal("0")
    cached_input_per_mtok: Decimal = Decimal("0")
    context_window: int = 0
    max_output_tokens: int = 0
    source_currency: str = "USD"
    note: str = ""
    wildcard: bool = False

    @property
    def free(self) -> bool:
        """输入输出均为 0 时视为零成本模型（如 Ollama）。"""
        return self.input_per_mtok == 0 and self.output_per_mtok == 0

    def cost(self, *, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> Decimal:
        """按 token 数算出 USD 花费。

        Args:
            prompt_tokens: 输入 token 总数（**含**缓存命中部分）。
            completion_tokens: 输出 token 数。
            cached_tokens: 命中前缀缓存的输入 token 数。

        Returns:
            量化到 8 位小数的花费。

        Raises:
            ValueError: 任一 token 数为负。
        """
        if prompt_tokens < 0 or completion_tokens < 0 or cached_tokens < 0:
            raise ValueError(
                f"token 数不能为负：prompt={prompt_tokens} completion={completion_tokens} "
                f"cached={cached_tokens}"
            )
        billable_prompt = max(0, prompt_tokens - cached_tokens)
        effective_cached = min(cached_tokens, prompt_tokens)
        total = (
            Decimal(billable_prompt) * self.input_per_mtok
            + Decimal(effective_cached) * self.cached_input_per_mtok
            + Decimal(completion_tokens) * self.output_per_mtok
        ) / TOKENS_PER_MILLION
        return quantize_money(total)


class PricingTable(BaseModel):
    """整张价目表。"""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    updated_at: str = ""
    fx: dict[str, Decimal] = Field(default_factory=lambda: {"USD": Decimal("1")})
    prices: dict[str, dict[str, ModelPrice]] = Field(default_factory=dict)
    source_path: str = ""

    # ------------------------------------------------------------------ 查询

    def lookup(self, provider: str, model: str) -> ModelPrice | None:
        """按「精确 → 最长前缀 → 通配」三级查找。

        Args:
            provider: Provider 注册名。
            model: 模型名。

        Returns:
            命中的 :class:`ModelPrice`；未命中返回 ``None``。
        """
        bucket = self.prices.get(_norm(provider))
        if not bucket:
            return None
        key = _norm(model)
        exact = bucket.get(key)
        if exact is not None:
            return exact

        best: ModelPrice | None = None
        best_len = -1
        for name, price in bucket.items():
            if name == "*":
                continue
            if key.startswith(name) and len(name) > best_len:
                best, best_len = price, len(name)
        if best is not None:
            return best
        return bucket.get("*")

    def providers(self) -> list[str]:
        """列出有价目的 Provider 名（字典序）。"""
        return sorted(self.prices)

    def models_of(self, provider: str) -> list[str]:
        """列出某 Provider 下的模型名（字典序，含 ``*``）。

        Args:
            provider: Provider 注册名。

        Returns:
            模型名列表。
        """
        return sorted(self.prices.get(_norm(provider), {}))

    def __len__(self) -> int:
        """价目条目总数。"""
        return sum(len(bucket) for bucket in self.prices.values())


def _norm(value: str) -> str:
    """名称归一化：去空白 + 小写。

    Args:
        value: 原始名称。

    Returns:
        归一化后的键。
    """
    return (value or "").strip().lower()


def _to_decimal(value: Any, field: str) -> Decimal:
    """把 YAML 里的数字安全转成 ``Decimal``（经字符串，避免二进制浮点误差）。

    Args:
        value: 原始值。
        field: 字段名（仅用于报错）。

    Returns:
        转换后的 :class:`~decimal.Decimal`。

    Raises:
        ConfigError: 值不是合法数字或为负。
    """
    try:
        result = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 —— InvalidOperation 等统一转配置错
        raise ConfigError(
            f"价目表字段 {field!r} 不是合法数字：{value!r}", details={"field": field}
        ) from exc
    if result < 0:
        raise ConfigError(f"价目表字段 {field!r} 不能为负：{value!r}", details={"field": field})
    return result


def _parse_entry(
    provider: str, model: str, raw: Mapping[str, Any], fx: Mapping[str, Decimal]
) -> ModelPrice:
    """解析一条价目并折算成 USD。

    Args:
        provider: Provider 名。
        model: 模型名。
        raw: YAML 原始映射。
        fx: 外汇表（``1 USD = fx[cur]`` 该币种）。

    Returns:
        USD 计价的 :class:`ModelPrice`。

    Raises:
        ConfigError: 币种未在 ``fx`` 中声明或汇率非正。
    """
    currency = str(raw.get("currency", "USD")).strip().upper()
    rate = fx.get(currency)
    if rate is None:
        raise ConfigError(
            f"价目表币种 {currency!r} 未在 fx 表中声明",
            details={"provider": provider, "model": model, "currency": currency},
        )
    if rate <= 0:
        raise ConfigError(
            f"价目表币种 {currency!r} 的汇率必须为正",
            details={"provider": provider, "model": model, "rate": str(rate)},
        )

    input_price = _to_decimal(raw.get("input", 0), f"{provider}.{model}.input") / rate
    output_price = _to_decimal(raw.get("output", 0), f"{provider}.{model}.output") / rate
    if "cached_input" in raw:
        cached_price = (
            _to_decimal(raw["cached_input"], f"{provider}.{model}.cached_input") / rate
        )
    else:
        cached_price = input_price

    return ModelPrice(
        provider=_norm(provider),
        model=_norm(model),
        input_per_mtok=input_price,
        output_per_mtok=output_price,
        cached_input_per_mtok=cached_price,
        context_window=int(raw.get("context", 0) or 0),
        max_output_tokens=int(raw.get("max_output", 0) or 0),
        source_currency=currency,
        note=str(raw.get("note", "")),
        wildcard=(model == "*"),
    )


def load_pricing(path: Path | str | None = None) -> PricingTable:
    """从 YAML 加载价目表（**不写全局单例**）。

    Args:
        path: 文件路径；``None`` 时用 :func:`default_pricing_path`。

    Returns:
        解析好的 :class:`PricingTable`。

    Raises:
        ConfigError: 文件不存在、不是合法 YAML 映射，或字段非法。
    """
    target = Path(path) if path is not None else default_pricing_path()
    if not target.is_absolute():
        from loopforge.core.config import project_root

        target = project_root() / target
    if not target.exists():
        raise ConfigError(
            f"价目表文件不存在：{target}", details={"path": str(target)}
        )
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"价目表 YAML 解析失败：{target}", details={"path": str(target), "error": str(exc)}
        ) from exc
    if not isinstance(raw, Mapping):
        raise ConfigError(
            f"价目表顶层必须是映射：{target}", details={"path": str(target)}
        )

    fx_raw = raw.get("fx") or {"USD": 1}
    if not isinstance(fx_raw, Mapping):
        raise ConfigError("价目表 fx 必须是映射", details={"path": str(target)})
    fx: dict[str, Decimal] = {
        str(cur).strip().upper(): _to_decimal(rate, f"fx.{cur}") for cur, rate in fx_raw.items()
    }
    fx.setdefault("USD", Decimal("1"))

    providers_raw = raw.get("providers") or {}
    if not isinstance(providers_raw, Mapping):
        raise ConfigError("价目表 providers 必须是映射", details={"path": str(target)})

    prices: dict[str, dict[str, ModelPrice]] = {}
    for provider, models in providers_raw.items():
        if not isinstance(models, Mapping):
            raise ConfigError(
                f"价目表 providers.{provider} 必须是映射", details={"provider": str(provider)}
            )
        bucket: dict[str, ModelPrice] = {}
        for model, entry in models.items():
            if not isinstance(entry, Mapping):
                raise ConfigError(
                    f"价目表 providers.{provider}.{model} 必须是映射",
                    details={"provider": str(provider), "model": str(model)},
                )
            bucket[_norm(str(model))] = _parse_entry(str(provider), str(model), entry, fx)
        prices[_norm(str(provider))] = bucket

    return PricingTable(
        version=int(raw.get("version", 1) or 1),
        updated_at=str(raw.get("updated_at", "")),
        fx=fx,
        prices=prices,
        source_path=str(target),
    )


def get_pricing_table() -> PricingTable:
    """返回进程级价目表单例（首次调用时按配置加载）。

    Returns:
        :class:`PricingTable` 单例。
    """
    global _TABLE  # noqa: PLW0603 —— 进程级单例，与 core.config 同构
    if _TABLE is None:
        with _LOCK:
            if _TABLE is None:
                _TABLE = _load_from_settings()
    return _TABLE


def _load_from_settings() -> PricingTable:
    """按 ``settings.providers.pricing_file`` 加载；失败时退回仓库内置表。

    Returns:
        价目表。

    Raises:
        ConfigError: 配置指定的与内置的价目表都加载失败。
    """
    candidates: list[Path] = []
    try:
        from loopforge.core.config import get_settings

        candidates.append(Path(get_settings().providers.pricing_file))
    except Exception:  # noqa: BLE001 —— 配置未初始化时直接用内置路径
        pass
    builtin = default_pricing_path()
    if builtin not in candidates:
        candidates.append(builtin)

    last_error: ConfigError | None = None
    for candidate in candidates:
        try:
            return load_pricing(candidate)
        except ConfigError as exc:
            last_error = exc
    raise last_error if last_error is not None else ConfigError("价目表加载失败")


def set_pricing_table(table: PricingTable | None) -> None:
    """替换（或清空）全局价目表单例 —— 测试与热更新用。

    Args:
        table: 新价目表；``None`` 表示清空以便下次重新加载。
    """
    global _TABLE  # noqa: PLW0603
    with _LOCK:
        _TABLE = table


def reload_pricing(path: Path | str | None = None) -> PricingTable:
    """重新加载价目表并写入全局单例。

    Args:
        path: 文件路径；``None`` 时走配置。

    Returns:
        新的价目表。
    """
    table = load_pricing(path) if path is not None else _load_from_settings()
    set_pricing_table(table)
    return table


def price_of(provider: str, model: str) -> ModelPrice:
    """查某个模型的价格。

    Args:
        provider: Provider 注册名。
        model: 模型名。

    Returns:
        :class:`ModelPrice`。

    Raises:
        ModelNotPricedError: 价目表里既无精确项也无通配项。
    """
    table = get_pricing_table()
    found = table.lookup(provider, model)
    if found is None:
        raise ModelNotPricedError(
            f"价目表中找不到 {provider}/{model}",
            details={
                "provider": provider,
                "model": model,
                "known_providers": table.providers(),
                "pricing_file": table.source_path,
            },
        )
    return found


def estimate_cost(
    provider: str,
    model: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> Decimal:
    """**调用前**成本预估（BudgetGuard 预检用）。

    Args:
        provider: Provider 注册名。
        model: 模型名。
        prompt_tokens: 预估输入 token。
        completion_tokens: 预估输出 token。
        cached_tokens: 预估命中缓存的 token。

    Returns:
        预估花费（USD，8 位小数）。

    Raises:
        ModelNotPricedError: 模型无价目。
    """
    return price_of(provider, model).cost(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
    )


def actual_cost(provider: str, model: str, usage: Usage) -> Decimal:
    """**调用后**实际成本（据真实 usage 回填账本）。

    Args:
        provider: Provider 注册名。
        model: 模型名。
        usage: 真实用量。

    Returns:
        实际花费（USD，8 位小数）。

    Raises:
        ModelNotPricedError: 模型无价目。
    """
    return price_of(provider, model).cost(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_tokens=usage.cached_tokens,
    )
