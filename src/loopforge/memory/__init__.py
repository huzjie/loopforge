"""上下文治理（WP5 · memory 包）。

四层记忆 + L0 不可压缩层的落地（``docs/ARCHITECTURE.md`` §8.2）：

* :class:`~loopforge.memory.manager.ContextManager` —— 冻结出口
  （``docs/TASK_BREAKDOWN.md`` §11.7），WP4/WP6/WP7 的唯一入口。
* :class:`~loopforge.memory.context_pack.ContextPack` —— 装配产物。
* :class:`~loopforge.memory.pinned.PinnedConstraintStore` —— L0 钉固约束。
* :class:`~loopforge.memory.budget_allocator.ContextBudgetAllocator` —— 层配额。
* :class:`~loopforge.memory.compressor.Compressor` —— 三级压缩阶梯（L1/L2 确定性）。
* :class:`~loopforge.memory.summarizer.Summarizer` —— 摘要树（L3）。
* ``layers`` —— 五层实现的注册表。
* ``retrieval`` —— BM25 / Embedding / 混合检索。

铁律（``TASK_BREAKDOWN`` §7.3.1）：
M1 L0 永不压缩；M2 原文永不物理删除；M3 每轮 ``check_fidelity``；
M4 压缩必须分级降级（禁止一上来调 LLM）。
"""

from __future__ import annotations

from loopforge.memory.budget_allocator import ContextBudgetAllocator, LayerBudget
from loopforge.memory.compressor import (
    CompactionResult,
    Compressor,
    FidelityReport,
    FidelityViolation,
)
from loopforge.memory.context_pack import ContextBlock, ContextPack
from loopforge.memory.manager import ContextManager
from loopforge.memory.pinned import PinnedConstraintStore
from loopforge.memory.salience import SalienceScorer
from loopforge.memory.summarizer import Summarizer

__all__ = [
    "CompactionResult",
    "Compressor",
    "ContextBlock",
    "ContextBudgetAllocator",
    "ContextManager",
    "ContextPack",
    "FidelityReport",
    "FidelityViolation",
    "LayerBudget",
    "PinnedConstraintStore",
    "SalienceScorer",
    "Summarizer",
]
