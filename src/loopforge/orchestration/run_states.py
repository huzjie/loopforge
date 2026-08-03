"""Run 状态机定义 —— 14 个状态与全部合法转移。

来源：``docs/ARCHITECTURE.md`` §6.1、``docs/TASK_BREAKDOWN.md`` §6.1.1。

冻结说明
--------
* 状态集以 :class:`~loopforge.core.enums.RunState`（WP1 冻结的 14 态）为准；
* 架构文档里的 ``BUDGET_HALTED`` 对应冻结枚举的 ``HALTED``、``VERIFYING``
  作为 RUNNING 的内部子阶段处理（枚举里没有该成员，不得凭空新增）；
* 本模块导出一个**进程级单例** :data:`RUN_STATE_MACHINE` 与纯函数表
  :data:`RUN_TRANSITION_TABLE`，仓储层 CAS 前用它做二次校验。
"""

from __future__ import annotations

from loopforge.core.enums import RunState
from loopforge.orchestration.state_machine import StateMachine, StateTable, TransitionRule

__all__ = [
    "RUN_STATES",
    "RUN_TRANSITIONS",
    "RUN_STATE_MACHINE",
    "RUN_TRANSITION_TABLE",
    "assert_run_transition",
    "is_legal_run_transition",
]

RUN_STATES: tuple[RunState, ...] = tuple(RunState)

#: (from, to, name) 三元组 —— 完整合法转移表。
#: 覆盖 ARCHITECTURE §6.1 的全部转移，并补齐冻结枚举中 PAUSING / CANCELLING
#: 两个中间态与 HALTED（预算 halt）的转移。
RUN_TRANSITIONS: tuple[tuple[RunState, RunState, str], ...] = (
    # 创建与启动
    (RunState.CREATED, RunState.PLANNING, "start"),
    (RunState.CREATED, RunState.CANCELLED, "cancel_before_start"),
    (RunState.PLANNING, RunState.READY, "plan_ready"),
    (RunState.PLANNING, RunState.FAILED, "plan_failed"),
    (RunState.READY, RunState.RUNNING, "dispatch_first"),
    # 审批
    (RunState.RUNNING, RunState.AWAITING_APPROVAL, "require_approval"),
    (RunState.AWAITING_APPROVAL, RunState.RUNNING, "approval_granted"),
    (RunState.AWAITING_APPROVAL, RunState.CANCELLED, "approval_rejected"),
    (RunState.AWAITING_APPROVAL, RunState.FAILED, "approval_timeout"),
    # 暂停 / 恢复
    (RunState.RUNNING, RunState.PAUSING, "pause_requested"),
    (RunState.PAUSING, RunState.PAUSED, "paused"),
    (RunState.PAUSED, RunState.RUNNING, "resume"),
    (RunState.PAUSED, RunState.CANCELLED, "cancel_paused"),
    (RunState.PAUSING, RunState.CANCELLED, "cancel_pausing"),
    # 预算 halt
    (RunState.RUNNING, RunState.HALTED, "budget_halted"),
    (RunState.HALTED, RunState.RUNNING, "budget_resumed"),
    (RunState.HALTED, RunState.FAILED, "budget_terminated"),
    # 恢复 / 对账
    (RunState.RUNNING, RunState.RECOVERING, "watchdog_anomaly"),
    (RunState.RECOVERING, RunState.RECONCILING, "has_unconfirmed"),
    (RunState.RECOVERING, RunState.RUNNING, "no_unconfirmed"),
    (RunState.RECONCILING, RunState.RUNNING, "reconciled"),
    (RunState.RECONCILING, RunState.AWAITING_APPROVAL, "uncertain_unsafe"),
    (RunState.RECONCILING, RunState.FAILED, "max_recovery"),
    # 收敛
    (RunState.RUNNING, RunState.SUCCEEDED, "all_tasks_terminal"),
    (RunState.RUNNING, RunState.FAILED, "max_loops_or_fatal"),
    (RunState.RUNNING, RunState.CANCELLING, "cancel_requested"),
    (RunState.CANCELLING, RunState.CANCELLED, "cancelled"),
    (RunState.RECOVERING, RunState.CANCELLED, "cancel_recovering"),
    (RunState.RECONCILING, RunState.CANCELLED, "cancel_reconciling"),
)


def _build_rules() -> list[TransitionRule[RunState]]:
    return [
        TransitionRule(from_state=src, to_state=dst, name=name)
        for src, dst, name in RUN_TRANSITIONS
    ]


def _build_table() -> StateTable[RunState]:
    return StateTable(
        states=RUN_STATES,
        transitions=[(src, dst) for src, dst, _name in RUN_TRANSITIONS],
        entity="run",
    )


#: 进程级单例状态机（初始态 CREATED）。
RUN_STATE_MACHINE: StateMachine[RunState] = StateMachine(
    states=RUN_STATES,
    transitions=_build_rules(),
    initial=RunState.CREATED,
    entity="run",
)

#: 纯函数转移表（供仓储 / 测试做静态查表）。
RUN_TRANSITION_TABLE: StateTable[RunState] = _build_table()


def is_legal_run_transition(from_state: RunState, to_state: RunState) -> bool:
    """判断 Run 转移是否合法（不抛异常）。"""
    return RUN_TRANSITION_TABLE.is_legal(from_state, to_state)


def assert_run_transition(from_state: RunState, to_state: RunState) -> None:
    """非法 Run 转移直接抛 :class:`~loopforge.core.errors.IllegalTransitionError`。"""
    RUN_TRANSITION_TABLE.is_legal(from_state, to_state) or _raise(from_state, to_state)


def _raise(from_state: RunState, to_state: RunState) -> None:
    from loopforge.core.errors import IllegalTransitionError

    raise IllegalTransitionError(
        f"非法 Run 转移：{from_state} -> {to_state}",
        details={"entity": "run", "from": str(from_state), "to": str(to_state)},
    )
