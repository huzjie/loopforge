"""成本估算器 —— 把 token 用量折算成 USD（``Decimal``）。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``budget/estimator.py``。

计价表从 ``config/pricing.yaml`` 读取（WP1 已提供该文件）；找不到时
退回内置启发式单价（OpenAI 类定价），保证任何环境都能估算。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from loopforge.core.config import project_root

__all__ = [
    "DEFAULT_INPUT_PRICE_PER_1K",
    "DEFAULT_OUTPUT_PRICE_PER_1K",
    "DEFAULT_CACHED_PRICE_PER_1K",
    "CostEstimator",
    "estimate_cost",
]

#: 默认输入单价（USD / 1K tokens）。
DEFAULT_INPUT_PRICE_PER_1K: Decimal = Decimal("0.0015")
#: 默认输出单价（USD / 1K tokens）。
DEFAULT_OUTPUT_PRICE_PER_1K: Decimal = Decimal("0.0020")
#: 默认缓存命中单价（USD / 1K tokens）。
DEFAULT_CACHED_PRICE_PER_1K: Decimal = Decimal("0.0003")

_ZERO = Decimal("0")


def _load_pricing(path: Path | None = None) -> dict[str, Any]:
    """读取 pricing YAML（不存在返回空字典）。"""
    candidate = path if path is not None else project_root() / "config" / "pricing.yaml"
    if not candidate.is_file():
        return {}
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return raw if isinstance(raw, dict) else {}


class CostEstimator:
    """成本估算器。

    :param pricing_file: 计价表路径；``None`` 用仓库默认 ``config/pricing.yaml``。
    :param default_input: 缺省输入单价（USD / 1K tokens）。
    :param default_output: 缺省输出单价。
    :param default_cached: 缺省缓存单价。
    """

    def __init__(
        self,
        pricing_file: Path | str | None = None,
        *,
        default_input: Decimal | None = None,
        default_output: Decimal | None = None,
        default_cached: Decimal | None = None,
    ) -> None:
        """初始化估算器。"""
        self._pricing: dict[str, Any] = _load_pricing(
            Path(pricing_file) if pricing_file is not None else None
        )
        self.default_input: Decimal = default_input or DEFAULT_INPUT_PRICE_PER_1K
        self.default_output: Decimal = default_output or DEFAULT_OUTPUT_PRICE_PER_1K
        self.default_cached: Decimal = default_cached or DEFAULT_CACHED_PRICE_PER_1K

    def price_for(self, provider: str, model: str, kind: str) -> Decimal:
        """取某模型某计价类型的单价（USD / 1K tokens）。

        :param provider: Provider 名（``openai`` / ``anthropic`` / ``mock`` ...）。
        :param model: 模型名。
        :param kind: ``input`` / ``output`` / ``cached``。
        :returns: 单价。
        """
        provider_table = self._pricing.get(provider)
        if isinstance(provider_table, dict):
            model_table = provider_table.get(model)
            if isinstance(model_table, dict):
                value = model_table.get(kind)
                if isinstance(value, (int, float, str)):
                    try:
                        return Decimal(str(value))
                    except Exception:  # noqa: BLE001 —— 非法单价回退默认
                        pass
        if kind == "output":
            return self.default_output
        if kind == "cached":
            return self.default_cached
        return self.default_input

    def estimate(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
    ) -> Decimal:
        """估算一次调用成本。

        :param provider: Provider 名。
        :param model: 模型名。
        :param prompt_tokens: 输入 token。
        :param completion_tokens: 输出 token。
        :param cached_tokens: 缓存命中 token（是 prompt_tokens 的子集）。
        :returns: USD 金额（Decimal）。
        """
        billable_prompt = max(0, prompt_tokens - cached_tokens)
        input_cost = (
            self.price_for(provider, model, "input") * Decimal(billable_prompt) / Decimal(1000)
        )
        cached_cost = (
            self.price_for(provider, model, "cached") * Decimal(max(0, cached_tokens)) / Decimal(1000)
        )
        output_cost = (
            self.price_for(provider, model, "output") * Decimal(max(0, completion_tokens)) / Decimal(1000)
        )
        return (input_cost + cached_cost + output_cost).quantize(Decimal("0.00000001"))

    def estimate_usage(self, usage: Any) -> Decimal:
        """按 ``Usage`` DTO 估算（兼容 providers.types.Usage）。

        :param usage: 含 ``prompt_tokens`` / ``completion_tokens`` / ``cached_tokens``。
        :returns: USD 金额。
        """
        return self.estimate(
            provider=getattr(usage, "provider", "mock"),
            model=getattr(usage, "model", "mock-large"),
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0)),
            completion_tokens=int(getattr(usage, "completion_tokens", 0)),
            cached_tokens=int(getattr(usage, "cached_tokens", 0)),
        )


def estimate_cost(
    provider: str,
    model: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    estimator: CostEstimator | None = None,
) -> Decimal:
    """便捷函数：估算一次调用成本。

    :param provider: Provider 名。
    :param model: 模型名。
    :param prompt_tokens: 输入 token。
    :param completion_tokens: 输出 token。
    :param cached_tokens: 缓存命中 token。
    :param estimator: 估算器；缺省用模块级共享实例。
    :returns: USD 金额（Decimal）。
    """
    return (estimator or _SHARED_ESTIMATOR).estimate(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
    )


#: 模块级共享估算器（无状态，可安全复用）。
_SHARED_ESTIMATOR: CostEstimator = CostEstimator()
