"""上下文预算分配器 —— 按层配额 + 空闲再分配 + 最小保障。

来源：``docs/ARCHITECTURE.md`` §8.2.1。

默认配额（``config/default.yaml`` ``memory.layer_ratios``）：:

    L0 PINNED     8%   （硬上限，永不压缩）
    L1 WORKING   40%
    L2 EPISODIC  22%
    L3 SEMANTIC  20%
    L4 MILESTONE 10%

**空闲配额再分配**：某层未用满时，剩余额度按 ``L0 > L1 > L4 > L2 > L3``
顺序分给其他层；L0 只增不减（它的配额是硬上限，但可以**再获得**别的层
让出的额度，用于容纳高优先级约束）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loopforge.core.enums import MemoryLayer

__all__ = [
    "DEFAULT_LAYER_RATIOS",
    "REALLOCATION_ORDER",
    "LayerBudget",
    "ContextBudgetAllocator",
]

#: 缺省五层配额（与 constants.LAYER_TOKEN_RATIOS 一致，供无配置时兜底）。
DEFAULT_LAYER_RATIOS: dict[MemoryLayer, float] = {
    MemoryLayer.PINNED: 0.08,
    MemoryLayer.WORKING: 0.40,
    MemoryLayer.EPISODIC: 0.22,
    MemoryLayer.SEMANTIC: 0.20,
    MemoryLayer.MILESTONE: 0.10,
}

#: 空闲再分配优先级：L0 > L1 > L4 > L2 > L3。
REALLOCATION_ORDER: tuple[MemoryLayer, ...] = (
    MemoryLayer.PINNED,
    MemoryLayer.WORKING,
    MemoryLayer.MILESTONE,
    MemoryLayer.EPISODIC,
    MemoryLayer.SEMANTIC,
)


@dataclass(slots=True)
class LayerBudget:
    """单个记忆层的预算视图。"""

    layer: MemoryLayer
    ratio: float = field(default=0.0, metadata={"ge": 0.0, "le": 1.0})
    quota_tokens: int = field(default=0, metadata={"ge": 0})
    used_tokens: int = field(default=0, metadata={"ge": 0})

    @property
    def remaining(self) -> int:
        """剩余可用 token。"""
        return max(0, self.quota_tokens - self.used_tokens)

    @property
    def is_exhausted(self) -> bool:
        """是否已用满配额。"""
        return self.remaining <= 0

    def can_fit(self, tokens: int) -> bool:
        """能否容纳 ``tokens`` 个 token。"""
        return tokens <= self.remaining

    def mark_used(self, tokens: int) -> None:
        """登记已使用 token。"""
        self.used_tokens = min(self.quota_tokens, self.used_tokens + max(0, tokens))

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return (
            f"LayerBudget(layer={self.layer.value}, quota={self.quota_tokens}, "
            f"used={self.used_tokens}, remaining={self.remaining})"
        )


class ContextBudgetAllocator:
    """把总 token 预算按五层配额切分，并执行空闲再分配。

    :param ratios: ``{MemoryLayer: 比例}``；缺省用 :data:`DEFAULT_LAYER_RATIOS`。
    :param min_guarantee_tokens: 每层最小保障 token 数（防止某层因配额
        过小被完全饿死；默认 64）。
    """

    def __init__(
        self,
        ratios: dict[MemoryLayer, float] | None = None,
        *,
        min_guarantee_tokens: int = 64,
    ) -> None:
        normalized = dict(DEFAULT_LAYER_RATIOS)
        if ratios:
            for layer, ratio in ratios.items():
                if layer is MemoryLayer.PINNED:
                    normalized[layer] = max(normalized.get(layer, 0.08), ratio)
                else:
                    normalized[layer] = max(0.0, ratio)
        self.ratios: dict[MemoryLayer, float] = normalized
        self.min_guarantee_tokens: int = max(0, min_guarantee_tokens)

    def allocate(self, token_budget: int) -> dict[MemoryLayer, LayerBudget]:
        """把 ``token_budget`` 切分到五层。

        :param token_budget: 总输入 token 预算（>0）。
        :returns: ``{MemoryLayer: LayerBudget}``，配额总和 <= ``token_budget``。
        """
        if token_budget <= 0:
            return {layer: LayerBudget(layer=layer, quota_tokens=0) for layer in MemoryLayer}

        total_ratio = sum(self.ratios.values())
        if total_ratio <= 0:
            raise ValueError("layer_ratios 之和必须大于 0")

        budgets: dict[MemoryLayer, LayerBudget] = {}
        for layer in MemoryLayer:
            # 比例按绝对值使用（不做整体归一化）；总和超过 1.0 时由
            # 下方 excess 收敛裁剪，裁剪顺序 L3 > L2 > L4 > L1，L0 最后动。
            ratio = self.ratios.get(layer, 0.0)
            quota = int(token_budget * ratio)
            # 最小保障：配额不足 min_guarantee 时补足（从总预算里扣）。
            quota = max(quota, min(self.min_guarantee_tokens, token_budget))
            budgets[layer] = LayerBudget(layer=layer, ratio=ratio, quota_tokens=quota)

        # 把超发的部分收敛回来：按 REALLOCATION_ORDER 反向削减。
        total = sum(budget.quota_tokens for budget in budgets.values())
        if total > token_budget:
            excess = total - token_budget
            # 削减顺序与再分配相反：L3 > L2 > L4 > L1，L0 最后才动。
            for layer in reversed(REALLOCATION_ORDER):
                if excess <= 0:
                    break
                budget = budgets[layer]
                cut = min(budget.quota_tokens - self.min_guarantee_tokens, excess)
                if cut <= 0:
                    continue
                budget.quota_tokens -= cut
                excess -= cut
        return budgets

    def reallocate(
        self,
        budgets: dict[MemoryLayer, LayerBudget],
        *,
        actual_usage: dict[MemoryLayer, int] | None = None,
    ) -> dict[MemoryLayer, LayerBudget]:
        """空闲配额再分配：未用满层的剩余额度按优先级分给其他层。

        :param budgets: :meth:`allocate` 的产物。
        :param actual_usage: ``{layer: 实际使用 token}``；缺省视为 0（全部空闲）。
        :returns: 新的配额视图（L0 只增不减）。
        """
        usage = dict(actual_usage or {})
        updated: dict[MemoryLayer, LayerBudget] = {}
        idle_pool: dict[MemoryLayer, int] = {}
        for layer, budget in budgets.items():
            used = max(0, usage.get(layer, 0))
            idle = max(0, budget.quota_tokens - used)
            updated[layer] = LayerBudget(
                layer=layer,
                ratio=budget.ratio,
                quota_tokens=budget.quota_tokens,
                used_tokens=used,
            )
            idle_pool[layer] = idle

        # 按优先级分配空闲额度（只增不减）。
        for target in REALLOCATION_ORDER:
            if target is MemoryLayer.PINNED:
                continue  # L0 是接受方，不主动让出
            available = idle_pool[target]
            if available <= 0:
                continue
            for receiver in REALLOCATION_ORDER:
                if receiver is target:
                    continue
                # 只给「配额未用满且本身也需要」的层；L0 优先收。
                receiver_budget = updated[receiver]
                receiver_used = max(0, usage.get(receiver, 0))
                receiver_free = max(0, receiver_budget.quota_tokens - receiver_used)
                if receiver_free <= 0 and receiver is not MemoryLayer.PINNED:
                    continue
                gift = min(available, max(0, receiver_budget.quota_tokens - receiver_used))
                if gift <= 0:
                    continue
                updated[receiver].quota_tokens += gift
                available -= gift
                if available <= 0:
                    break
            idle_pool[target] = available
        return updated
