"""Task 状态机定义 —— 10 个状态与全部合法转移（含租约相关转移）。

来源：``docs/ARCHITECTURE.md`` §6.2、``docs/TASK_BREAKDOWN.md`` §6.1.1。

冻结说明
--------
* 状态集以 :class:`~loopforge.core.enums.TaskState`（WP1 冻结的 10 态）为准；
* 架构文档里的 ``DRAFT`` / ``ABANDONED`` / ``VERIFYING`` 分别对应冻结枚举的
  ``CREATED`` / ``CANCELLED`` / 及 ``IN_REVIEW`` 的内部子阶段；
* 关键解耦（§8.3.4）：``READY -> FAILED`` 只由 ``reclaim_count`` 超限触发；
  ``attempt_count`` 超限走 ``ACTIVE -> FAILED``。两条路径互不影响。
"""

from __future__ import annotations

from loopforge.core.enums import TaskState
from loopforge.orchestration.state_machine import StateMachine, StateTable, TransitionRule

__all__ = [
    "TASK_STATES",
    "TASK_TRANSITIONS",
    "TASK_STATE_MACHINE",
    "TASK_TRANSITION_TABLE",
    "assert_task_transition",
    "is_legal_task_transition",
]

TASK_STATES: tuple[TaskState, ...] = tuple(TaskState)

#: (from, to, name) 三元组 —— 完整合法转移表。
TASK_TRANSITIONS: tuple[tuple[TaskState, TaskState, str], ...] = (
    # 创建与就绪
    (TaskState.CREATED, TaskState.READY, "deps_satisfied"),
    (TaskState.CREATED, TaskState.CANCELLED, "voided"),
    # 租约流转
    (TaskState.READY, TaskState.LEASED, "lease_acquired"),
    (TaskState.LEASED, TaskState.READY, "lease_reclaimed"),
    (TaskState.LEASED, TaskState.ACTIVE, "worker_takeover"),
    (TaskState.ACTIVE, TaskState.READY, "abandoned_or_reclaimed"),
    # 阻塞
    (TaskState.ACTIVE, TaskState.BLOCKED, "waiting_dependency"),
    (TaskState.BLOCKED, TaskState.READY, "unblocked"),
    (TaskState.READY, TaskState.BLOCKED, "dependency_missing"),
    (TaskState.BLOCKED, TaskState.CANCELLED, "cancel_blocked"),
    # 评审 / 收尾
    (TaskState.ACTIVE, TaskState.IN_REVIEW, "pr_created"),
    (TaskState.IN_REVIEW, TaskState.DONE, "review_passed"),
    (TaskState.IN_REVIEW, TaskState.READY, "review_rejected"),
    (TaskState.IN_REVIEW, TaskState.CANCELLED, "cancel_in_review"),
    # 失败 / 终态
    (TaskState.ACTIVE, TaskState.FAILED, "fatal_or_max_attempts"),
    (TaskState.READY, TaskState.FAILED, "max_reclaims"),
    (TaskState.LEASED, TaskState.FAILED, "fatal_after_lease"),
    (TaskState.CREATED, TaskState.FAILED, "creation_failed"),
    (TaskState.BLOCKED, TaskState.FAILED, "blocked_fatal"),
)


def _build_rules() -> list[TransitionRule[TaskState]]:
    return [
        TransitionRule(from_state=src, to_state=dst, name=name)
        for src, dst, name in TASK_TRANSITIONS
    ]


def _build_table() -> StateTable[TaskState]:
    return StateTable(
        states=TASK_STATES,
        transitions=[(src, dst) for src, dst, _name in TASK_TRANSITIONS],
        entity="task",
    )


#: 进程级单例状态机（初始态 CREATED）。
TASK_STATE_MACHINE: StateMachine[TaskState] = StateMachine(
    states=TASK_STATES,
    transitions=_build_rules(),
    initial=TaskState.CREATED,
    entity="task",
)

#: 纯函数转移表（供仓储 / 测试做静态查表）。
TASK_TRANSITION_TABLE: StateTable[TaskState] = _build_table()


def is_legal_task_transition(from_state: TaskState, to_state: TaskState) -> bool:
    """判断 Task 转移是否合法（不抛异常）。"""
    return TASK_TRANSITION_TABLE.is_legal(from_state, to_state)


def assert_task_transition(from_state: TaskState, to_state: TaskState) -> None:
    """非法 Task 转移直接抛 :class:`~loopforge.core.errors.IllegalTransitionError`。"""
    TASK_TRANSITION_TABLE.is_legal(from_state, to_state) or _raise(from_state, to_state)


def _raise(from_state: TaskState, to_state: TaskState) -> None:
    from loopforge.core.errors import IllegalTransitionError

    raise IllegalTransitionError(
        f"非法 Task 转移：{from_state} -> {to_state}",
        details={"entity": "task", "from": str(from_state), "to": str(to_state)},
    )
