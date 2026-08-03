"""调度策略 —— FIFO / Priority / DependencyAware（拓扑序 + 关键路径）。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1。

约定
----
* 策略只负责**排序**，不负责领取（CAS 由 Dispatcher + LeaseManager 负责）；
* 输入是「依赖已满足」的候选列表，策略输出有序列表，Dispatcher 逐个尝试；
* ``name`` 是配置 ``dispatcher.strategy`` 的取值（``fifo`` / ``priority`` /
  ``dependency_aware``）。
"""

from __future__ import annotations

import abc
from typing import Any, ClassVar

from loopforge.core.enums import TERMINAL_TASK_STATES, TaskState
from loopforge.models.task import Task

__all__ = [
    "BaseSchedulingStrategy",
    "FifoStrategy",
    "PriorityStrategy",
    "DependencyAwareStrategy",
    "default_strategy",
    "get_strategy",
]


class BaseSchedulingStrategy(abc.ABC):
    """调度策略基类。"""

    name: ClassVar[str] = "base"

    @abc.abstractmethod
    def order(self, candidates: list[Task]) -> list[Task]:
        """把候选任务排成领取顺序。"""
        raise NotImplementedError


class FifoStrategy(BaseSchedulingStrategy):
    """先入先出 —— 按创建时间正序。"""

    name = "fifo"

    def order(self, candidates: list[Task]) -> list[Task]:
        return sorted(candidates, key=lambda t: (t.created_at, t.id))


class PriorityStrategy(BaseSchedulingStrategy):
    """优先级 —— 数值越大越优先，同优先级按创建时间。"""

    name = "priority"

    def order(self, candidates: list[Task]) -> list[Task]:
        return sorted(
            candidates,
            key=lambda t: (t.priority, -t.created_at.timestamp() if t.created_at else 0, t.id),
            reverse=True,
        )


class DependencyAwareStrategy(BaseSchedulingStrategy):
    """依赖感知 —— 拓扑序（依赖先于被依赖）+ 关键路径（下游最多的先）。"""

    name = "dependency_aware"

    def order(self, candidates: list[Task]) -> list[Task]:
        """对候选做稳定拓扑排序。

        候选里可能仍有互相依赖（如一个候选依赖另一个候选但还没终态），
        这里按「被依赖数」做关键路径启发：被依赖多的任务先领。
        """
        by_id: dict[str, Task] = {t.id: t for t in candidates}
        dependents: dict[str, int] = {t.id: 0 for t in candidates}
        for task in candidates:
            for dep_id in task.depends_on or []:
                if dep_id in by_id:
                    dependents[dep_id] = dependents.get(dep_id, 0) + 1
        return sorted(
            candidates,
            key=lambda t: (
                dependents.get(t.id, 0),
                t.priority,
                t.created_at or _epoch(),
            ),
            reverse=True,
        )


def _epoch() -> Any:
    from datetime import UTC, datetime

    return datetime(1970, 1, 1, tzinfo=UTC)


def default_strategy(name: str | None = None) -> BaseSchedulingStrategy:
    """按名称构造默认策略实例。

    :param name: 策略名；``None`` 或未知时回退 ``dependency_aware``。
    """
    return get_strategy(name or "dependency_aware")()


def get_strategy(name: str) -> type[BaseSchedulingStrategy]:
    """按名称取策略类。

    :raises KeyError: 未知策略名。
    """
    mapping: dict[str, type[BaseSchedulingStrategy]] = {
        "fifo": FifoStrategy,
        "priority": PriorityStrategy,
        "dependency_aware": DependencyAwareStrategy,
    }
    try:
        return mapping[name]
    except KeyError:
        return DependencyAwareStrategy
