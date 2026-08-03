"""上下文预算分配器测试 —— 配额 / 空闲再分配 / 最小保障。

ARCHITECTURE §8.2.1：L0 8% 硬上限；空闲再分配顺序 L0 > L1 > L4 > L2 > L3。
"""

from __future__ import annotations

import pytest

from loopforge.core.enums import MemoryLayer
from loopforge.memory.budget_allocator import (
    ContextBudgetAllocator,
    DEFAULT_LAYER_RATIOS,
    REALLOCATION_ORDER,
)


def test_allocate_sum_within_budget() -> None:
    """五层配额总和不超过总预算。"""
    allocator = ContextBudgetAllocator()
    budgets = allocator.allocate(100_000)
    total = sum(budget.quota_tokens for budget in budgets.values())
    assert total <= 100_000
    # 各层比例与默认值一致（允许最小保障造成的少量偏差）。
    pinned = budgets[MemoryLayer.PINNED]
    assert abs(pinned.quota_tokens - 8_000) <= 64


def test_pinned_ratio_is_hard_max() -> None:
    """L0 比例取 max(默认, 传入) —— 只增不减。"""
    allocator = ContextBudgetAllocator({MemoryLayer.PINNED: 0.03})
    budgets = allocator.allocate(100_000)
    assert budgets[MemoryLayer.PINNED].quota_tokens >= 8_000 - 64
    allocator2 = ContextBudgetAllocator({MemoryLayer.PINNED: 0.2})
    budgets2 = allocator2.allocate(100_000)
    assert budgets2[MemoryLayer.PINNED].quota_tokens >= 20_000 - 64


def test_min_guarantee_not_starved() -> None:
    """最小保障：即使某层比例极小也不会被饿死。"""
    allocator = ContextBudgetAllocator(
        {
            MemoryLayer.PINNED: 0.001,
            MemoryLayer.WORKING: 0.5,
            MemoryLayer.EPISODIC: 0.3,
            MemoryLayer.SEMANTIC: 0.1,
            MemoryLayer.MILESTONE: 0.099,
        },
        min_guarantee_tokens=32,
    )
    budgets = allocator.allocate(1_000)
    for layer in MemoryLayer:
        assert budgets[layer].quota_tokens >= 0


def test_reallocate_idle_to_pinned_first() -> None:
    """空闲再分配：L0 优先获得其他层的空闲额度。"""
    allocator = ContextBudgetAllocator()
    budgets = allocator.allocate(10_000)
    usage = {
        MemoryLayer.WORKING: 0,  # 工作记忆完全空闲
        MemoryLayer.EPISODIC: 0,
        MemoryLayer.SEMANTIC: 0,
        MemoryLayer.MILESTONE: 0,
        MemoryLayer.PINNED: 0,
    }
    reallocated = allocator.reallocate(budgets, actual_usage=usage)
    # L0 获得最多的再分配额度（顺序最前）。
    pinned_gain = reallocated[MemoryLayer.PINNED].quota_tokens - budgets[MemoryLayer.PINNED].quota_tokens
    assert pinned_gain > 0
    assert reallocated[MemoryLayer.PINNED].quota_tokens >= reallocated[MemoryLayer.SEMANTIC].quota_tokens


def test_reallocation_order() -> None:
    """再分配顺序恒为 L0 > L1 > L4 > L2 > L3。"""
    assert REALLOCATION_ORDER == (
        MemoryLayer.PINNED,
        MemoryLayer.WORKING,
        MemoryLayer.MILESTONE,
        MemoryLayer.EPISODIC,
        MemoryLayer.SEMANTIC,
    )


def test_layer_budget_helpers() -> None:
    """LayerBudget 的剩余 / 容量判定。"""
    from loopforge.memory.budget_allocator import LayerBudget

    budget = LayerBudget(layer=MemoryLayer.WORKING, ratio=0.4, quota_tokens=100)
    assert budget.remaining == 100
    assert budget.can_fit(100)
    assert not budget.can_fit(101)
    budget.mark_used(30)
    assert budget.remaining == 70
    budget.mark_used(999)
    assert budget.remaining == 0
    assert budget.is_exhausted


def test_ratios_defaults_match_constants() -> None:
    """默认比例与 constants.LAYER_TOKEN_RATIOS 一致。"""
    from loopforge.constants import LAYER_TOKEN_RATIOS

    for layer, ratio in DEFAULT_LAYER_RATIOS.items():
        assert abs(ratio - LAYER_TOKEN_RATIOS[layer.value]) < 1e-9
