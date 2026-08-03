"""调度策略注册 —— 约定式自动发现。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1 / §13.3。

本文件只做策略注册表的初始化与导出，不承载具体策略逻辑
（具体策略在 :mod:`loopforge.orchestration.scheduling.strategies`）。
"""

from __future__ import annotations

from loopforge.core.registry import Registry
from loopforge.orchestration.scheduling.strategies import (
    BaseSchedulingStrategy,
    DependencyAwareStrategy,
    FifoStrategy,
    PriorityStrategy,
    default_strategy,
    get_strategy,
)

__all__ = [
    "BaseSchedulingStrategy",
    "FifoStrategy",
    "PriorityStrategy",
    "DependencyAwareStrategy",
    "default_strategy",
    "get_strategy",
    "scheduling_strategies",
]

#: 进程级调度策略注册表。
scheduling_strategies: Registry[type[BaseSchedulingStrategy]] = Registry(
    "scheduling_strategy", initial={"fifo": FifoStrategy}
)

scheduling_strategies.register("priority", PriorityStrategy, override=True)
scheduling_strategies.register("dependency_aware", DependencyAwareStrategy, override=True)
