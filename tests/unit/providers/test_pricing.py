"""价目表与成本换算单元测试。

覆盖：
* ``load_pricing`` 解析内置 ``config/pricing.yaml``；
* ``price_of`` 精确 / 前缀 / 通配三级查找；
* ``estimate_cost`` / ``actual_cost`` 两遍成本计算；
* 缓存计价（cached_tokens 拆算）；
* 未定价模型抛 ``ModelNotPricedError``；
* 金额全程 Decimal 且量化到 8 位小数。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from loopforge.core.errors import ConfigError
from loopforge.providers.pricing import (
    MONEY_QUANT,
    ModelNotPricedError,
    ModelPrice,
    PricingTable,
    actual_cost,
    estimate_cost,
    load_pricing,
    price_of,
    quantize_money,
    set_pricing_table,
)
from loopforge.providers.types import Usage


@pytest.fixture(autouse=True)
def _clean_pricing_table() -> None:
    """每个测试结束后清空全局价目表单例，避免串味。"""
    yield
    set_pricing_table(None)


def test_load_builtin_pricing() -> None:
    """内置价目表可加载且包含六类 Provider。"""
    table = load_pricing()
    providers = set(table.providers())
    assert {"mock", "openai", "dashscope", "anthropic", "deepseek", "ollama"} <= providers


def test_price_of_exact() -> None:
    """精确模型名命中。"""
    price = price_of("openai", "gpt-4o")
    assert price.provider == "openai"
    assert price.model == "gpt-4o"
    assert price.input_per_mtok == Decimal("2.50")
    assert price.output_per_mtok == Decimal("10.00")


def test_price_of_prefix_match() -> None:
    """带日期后缀的快照名应命中前缀。"""
    price = price_of("dashscope", "qwen3.8-max-2026-08-01")
    assert price.model == "qwen3.8-max"


def test_price_of_wildcard() -> None:
    """未列出模型应命中通配。"""
    price = price_of("ollama", "llama3.3-70b")
    assert price is not None
    assert price.free  # ollama 全为 0


def test_price_of_unknown_raises() -> None:
    """完全未定价的 provider/model 应抛 ModelNotPricedError。"""
    with pytest.raises(ModelNotPricedError):
        price_of("no_such_provider", "no-such-model")


def test_lookup_three_levels() -> None:
    """三级查找顺序：精确 > 前缀 > 通配。"""
    table = load_pricing()
    assert table.lookup("openai", "gpt-4o").model == "gpt-4o"  # 精确
    assert table.lookup("dashscope", "qwen3.8-max-2026-08-01").model == "qwen3.8-max"  # 前缀
    assert table.lookup("openai", "random-model-xyz").model == "*"  # 通配


def test_estimate_cost_mock_large() -> None:
    """estimate_cost 使用 input/output 单价。"""
    cost = estimate_cost("mock", "mock-large", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == Decimal("2.00000000")  # 0.5 + 1.5


def test_estimate_cost_cached() -> None:
    """缓存 token 走 cached_input 单价。"""
    cost = estimate_cost(
        "mock",
        "mock-large",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cached_tokens=1_000_000,
    )
    assert cost == Decimal("0.05000000")  # 全部命中缓存


def test_estimate_cost_partial_cache() -> None:
    """部分缓存：未命中部分按 input 价，命中部分按 cached 价。"""
    cost = estimate_cost(
        "mock",
        "mock-large",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cached_tokens=400_000,
    )
    # 600k * 0.5 + 400k * 0.05 = 0.30 + 0.02 = 0.32
    assert cost == Decimal("0.32000000")


def test_actual_cost_uses_usage() -> None:
    """actual_cost 据真实 usage 回填。"""
    usage = Usage(prompt_tokens=100, completion_tokens=200, cached_tokens=0)
    cost = actual_cost("mock", "mock-large", usage)
    # (100*0.5 + 200*1.5) / 1e6 = 0.00005 + 0.00030 = 0.00035
    expected = Decimal("0.00035000")
    assert cost == expected


def test_decimal_precision() -> None:
    """金额始终为 Decimal 且量化到 8 位小数。"""
    cost = estimate_cost("openai", "gpt-4o", prompt_tokens=12345, completion_tokens=6789)
    assert isinstance(cost, Decimal)
    assert cost.as_tuple().exponent == -8


def test_quantize_money() -> None:
    """quantize_money 四舍五入到 8 位。"""
    assert quantize_money(Decimal("1.123456789")) == Decimal("1.12345679")
    assert quantize_money(Decimal("1.123456781")) == Decimal("1.12345678")


def test_negative_tokens_rejected() -> None:
    """负 token 数应抛 ValueError。"""
    with pytest.raises(ValueError):
        ModelPrice(provider="x", model="y").cost(
            prompt_tokens=-1, completion_tokens=0
        )


def test_pricing_table_len() -> None:
    """价目表条目数大于 0。"""
    table = load_pricing()
    assert len(table) > 10


def test_money_quant_constant() -> None:
    """金额量化精度为 8 位小数。"""
    assert MONEY_QUANT == Decimal("0.00000001")
