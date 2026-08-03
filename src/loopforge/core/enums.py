"""全局枚举 —— LoopForge 的公共词汇表（**已冻结**）。

来源：``docs/TASK_BREAKDOWN.md`` §11.1 / ``docs/ARCHITECTURE.md`` §6。

.. warning::
   本模块由 WP1 owner 独家维护。**成员名即字符串值**，数据库与 API 直接使用，
   不得改名、不得删除、不得调整既有成员顺序。确需新增只能**追加在枚举段尾**，
   且必须走 ``TASK_BREAKDOWN`` §13.5 的变更流程。

一共 11 组枚举：

==================  ====  ============================================
枚举                成员  用途
==================  ====  ============================================
RunState             14   Run 生命周期状态机
TaskState            10   Task 生命周期状态机
StepState             6   Step（幂等单元）状态机
LeaseState            4   租约状态机
SideEffectClass       4   工具副作用分级（崩溃恢复决策依据）
PolicyDecision        3   策略引擎裁决结果
MemoryLayer           5   上下文记忆分层
RoleName              5   智能体角色
Severity              6   事件 / 日志严重级
BudgetScope           3   预算作用域
BudgetAction          3   预算触发动作
==================  ====  ============================================
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "BudgetAction",
    "BudgetScope",
    "LeaseState",
    "MemoryLayer",
    "PolicyDecision",
    "RoleName",
    "RunState",
    "Severity",
    "SideEffectClass",
    "StepState",
    "TaskState",
    "ACTIVE_RUN_STATES",
    "TERMINAL_RUN_STATES",
    "TERMINAL_TASK_STATES",
    "SEVERITY_ORDER",
    "ALL_ENUMS",
]


class RunState(StrEnum):
    """Run 状态机（14 个状态）。"""

    CREATED = "CREATED"
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    RECONCILING = "RECONCILING"
    RECOVERING = "RECOVERING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    HALTED = "HALTED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class TaskState(StrEnum):
    """Task 状态机（10 个状态）。"""

    CREATED = "CREATED"
    READY = "READY"
    LEASED = "LEASED"
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    IN_REVIEW = "IN_REVIEW"
    DEFERRED = "DEFERRED"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepState(StrEnum):
    """Step 状态机（6 个状态）——幂等三段式的核心。"""

    PENDING = "PENDING"
    INTENT_RECORDED = "INTENT_RECORDED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"
    UNCERTAIN = "UNCERTAIN"


class LeaseState(StrEnum):
    """租约状态机（4 个状态）。"""

    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class SideEffectClass(StrEnum):
    """工具副作用分级 —— 崩溃恢复时决定「能否重放 / 能否探测」。"""

    PURE = "PURE"
    IDEMPOTENT = "IDEMPOTENT"
    PROBEABLE = "PROBEABLE"
    UNSAFE = "UNSAFE"


class PolicyDecision(StrEnum):
    """策略引擎裁决结果。"""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class MemoryLayer(StrEnum):
    """上下文记忆分层。"""

    PINNED = "PINNED"
    WORKING = "WORKING"
    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    MILESTONE = "MILESTONE"


class RoleName(StrEnum):
    """智能体角色。"""

    PLANNER = "PLANNER"
    CODER = "CODER"
    REVIEWER = "REVIEWER"
    TESTER = "TESTER"
    SUMMARIZER = "SUMMARIZER"


class Severity(StrEnum):
    """事件与日志严重级。"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    NOTICE = "NOTICE"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class BudgetScope(StrEnum):
    """预算作用域。"""

    RUN = "RUN"
    DAY = "DAY"
    ORG = "ORG"


class BudgetAction(StrEnum):
    """预算阈值触发的动作。"""

    WARN = "WARN"
    THROTTLE = "THROTTLE"
    HALT = "HALT"


# ---------------------------------------------------------------------------
# 派生常量集合（只读，供 WP4/WP7 判定用；不属于冻结枚举本体，可安全扩展）
# ---------------------------------------------------------------------------

#: 「活跃」Run 状态集合 —— Dispatcher 与 Watchdog 的扫描范围。
ACTIVE_RUN_STATES: frozenset[RunState] = frozenset(
    {
        RunState.PLANNING,
        RunState.READY,
        RunState.RUNNING,
        RunState.PAUSING,
        RunState.RECONCILING,
        RunState.RECOVERING,
        RunState.AWAITING_APPROVAL,
        RunState.CANCELLING,
    }
)

#: Run 终态集合 —— 进入后不再迁出。
TERMINAL_RUN_STATES: frozenset[RunState] = frozenset(
    {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED}
)

#: Task 终态集合。
TERMINAL_TASK_STATES: frozenset[TaskState] = frozenset(
    {TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}
)

#: Severity 的偏序（数值越大越严重），用于日志过滤与告警阈值比较。
SEVERITY_ORDER: dict[Severity, int] = {
    Severity.DEBUG: 10,
    Severity.INFO: 20,
    Severity.NOTICE: 25,
    Severity.WARNING: 30,
    Severity.ERROR: 40,
    Severity.CRITICAL: 50,
}

#: 11 组冻结枚举的清单，供 CI 一致性校验遍历。
ALL_ENUMS: tuple[type[StrEnum], ...] = (
    RunState,
    TaskState,
    StepState,
    LeaseState,
    SideEffectClass,
    PolicyDecision,
    MemoryLayer,
    RoleName,
    Severity,
    BudgetScope,
    BudgetAction,
)
