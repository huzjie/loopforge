"""阶段注册 —— 约定式自动发现。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1 / §13.3。

新增阶段 = 只新建自己的文件（继承 :class:`~loopforge.orchestration.phases.base.BasePhase`），
本文件无需改动；:func:`default_phases` 返回循环的默认阶段顺序。
"""

from __future__ import annotations

from typing import Any

from loopforge.orchestration.phases.act import ActPhase
from loopforge.orchestration.phases.base import BasePhase, PhaseContext, PhaseResult
from loopforge.orchestration.phases.commit import CommitPhase
from loopforge.orchestration.phases.evolve import EvolvePhase
from loopforge.orchestration.phases.observe import ObservePhase
from loopforge.orchestration.phases.plan import PlanPhase
from loopforge.orchestration.phases.verify import VerifyPhase

__all__ = [
    "BasePhase",
    "PhaseContext",
    "PhaseResult",
    "PlanPhase",
    "ActPhase",
    "ObservePhase",
    "VerifyPhase",
    "CommitPhase",
    "EvolvePhase",
    "default_phases",
    "PHASE_ORDER",
]

#: 循环阶段的默认执行顺序。
PHASE_ORDER: tuple[str, ...] = ("PLAN", "ACT", "OBSERVE", "VERIFY", "COMMIT", "EVOLVE")


def default_phases() -> list[BasePhase]:
    """构造默认阶段实例列表（按循环顺序）。"""
    return [
        PlanPhase(),
        ActPhase(),
        ObservePhase(),
        VerifyPhase(),
        CommitPhase(),
        EvolvePhase(),
    ]
