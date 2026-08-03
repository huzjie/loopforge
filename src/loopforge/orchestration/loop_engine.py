"""循环引擎 —— ``plan → act → observe → verify → commit → evolve`` 的驱动核心。

来源：``docs/ARCHITECTURE.md`` §6 / ``docs/TASK_BREAKDOWN.md`` §6.1.1 / §11.6。

冻结契约（§11.6）::

    class LoopEngine:
        async def run_loop(self, *, run_id: str, task_id: str, lease: Lease) -> LoopOutcome: ...

设计要点
--------
1. **轮次上限与单轮超时双保险**：``run.max_loops`` 与 ``loop_timeout_s``
   任一触发即安全收敛（PAUSED / FAILED），绝不无限跑；
2. **每个 Phase 结束必写检查点**：``kill -9`` 最多损失一个 Phase 的进度；
3. **VERIFY 失败生成修复子任务回流**：跳过 COMMIT，进入下一轮重新规划；
4. **事件计数**：引擎启动时挂一个计数订阅者，统计本轮为该 Run 发布的
   事件数（``events_published`` 供冒烟断言）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.clock import Clock, get_clock
from loopforge.core.config import Settings, get_settings
from loopforge.core.enums import RunState, Severity, TaskState
from loopforge.core.errors import LoopForgeError, wrap_exception
from loopforge.core.event_bus import EventBus, RecordingSubscriber, get_event_bus
from loopforge.core.event_types import EventType
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork
from loopforge.models.lease import Lease
from loopforge.orchestration.checkpoint import CheckpointStore
from loopforge.orchestration.idempotency import IdempotencyGuard
from loopforge.orchestration.lease_manager import LeaseManager
from loopforge.orchestration.phases import (
    BasePhase,
    PhaseContext,
    PhaseResult,
    default_phases,
)

__all__ = ["EngineDeps", "LoopOutcome", "LoopEngine"]


@dataclass(slots=True)
class EngineDeps:
    """LoopEngine 的依赖集合。

    Attributes:
        provider: LLM Provider（WP2）。
        executor: 执行器（WP3）。
        tool_registry / tool_dispatcher: 工具（WP3）。
        context_manager: 上下文管理器（WP5）。
        budget_guard: 预算守卫（WP5）；可为 ``None``。
        policy_engine: 策略引擎（WP5）；可为 ``None``。
        idempotency: 幂等守卫（本包）。
        lease_manager: 租约管理器（本包）。
        checkpoint: 检查点存储（本包）。
        verification: 验证流水线（WP3）。
        settings: 全局配置。
        workspace: 工作区句柄。
    """

    provider: Any
    executor: Any
    tool_registry: Any
    tool_dispatcher: Any
    context_manager: Any
    idempotency: IdempotencyGuard
    lease_manager: LeaseManager
    checkpoint: CheckpointStore
    verification: Any
    settings: Settings
    workspace: Any
    budget_guard: Any = None
    policy_engine: Any = None


class LoopOutcome(BaseModel):
    """一次 run_loop 的结果。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    task_id: str
    loop_index: int = 0
    outcome: str = ""
    message: str = ""
    phase_results: list[dict[str, Any]] = Field(default_factory=list)
    steps_created: int = 0
    events_published: int = 0
    commit_sha: str = ""
    branch: str = ""
    duration_ms: int = 0


class LoopEngine:
    """循环引擎（冻结契约 §11.6）。

    :param deps: 依赖集合；``None`` 时由 :meth:`from_defaults` 构造。
    :param phases: 阶段列表；``None`` 使用默认六阶段。
    :param clock: 时间源；``None`` 使用全局。
    :param session_factory: session 工厂；``None`` 使用全局。
    """

    def __init__(
        self,
        *,
        deps: EngineDeps | None = None,
        phases: list[BasePhase] | None = None,
        clock: Clock | None = None,
        session_factory: Any | None = None,
    ) -> None:
        self._deps: EngineDeps = deps if deps is not None else self.from_defaults()
        self._phases: list[BasePhase] = phases if phases is not None else default_phases()
        self._clock: Clock = clock or get_clock()
        self._session_factory: Any = (
            session_factory if session_factory is not None else get_session_factory()
        )

    @classmethod
    def from_defaults(cls) -> EngineDeps:
        """用全局配置构造依赖（测试 / CLI 便捷入口）。"""
        settings = get_settings()
        from loopforge.executors import create_executor
        from loopforge.governance.policy.engine import PolicyEngine
        from loopforge.memory import ContextManager
        from loopforge.orchestration.checkpoint import CheckpointStore
        from loopforge.orchestration.idempotency import IdempotencyGuard
        from loopforge.orchestration.lease_manager import LeaseManager
        from loopforge.providers.registry import create_mock_provider
        from loopforge.tools import ToolDispatcher, ToolRegistry
        from loopforge.verification import VerificationPipeline

        provider = create_mock_provider()
        executor = create_executor()
        registry = ToolRegistry()
        registry.autoload_builtins()
        dispatcher = ToolDispatcher(registry)
        return EngineDeps(
            provider=provider,
            executor=executor,
            tool_registry=registry,
            tool_dispatcher=dispatcher,
            context_manager=ContextManager(),
            idempotency=IdempotencyGuard(),
            lease_manager=LeaseManager(),
            checkpoint=CheckpointStore(),
            verification=VerificationPipeline(),
            settings=settings,
            workspace=None,
            budget_guard=None,
            policy_engine=PolicyEngine(),
        )

    # -- 主入口 -------------------------------------------------------------

    async def run_loop(
        self, *, run_id: str, task_id: str, lease: Lease
    ) -> LoopOutcome:
        """驱动一个 Task 的循环直到轮次耗尽 / 超时 / 收敛。

        :param run_id: 目标 Run。
        :param task_id: 目标 Task。
        :param lease: 当前 Worker 持有的租约。
        :returns: :class:`LoopOutcome`。
        """
        started = self._clock.monotonic()
        loop_timeout_s = float(self._deps.settings.orchestration.loop_timeout_s)

        # 事件计数订阅。
        bus: EventBus = get_event_bus()
        counter = RecordingSubscriber()
        sub_id = bus.subscribe(counter)
        try:
            # 状态收口：Run → RUNNING，Task LEASED → ACTIVE。
            await self._start_run(run_id, task_id, lease)

            loop_index = await self._current_loop(run_id)
            phase_results: list[PhaseResult] = []
            outcome = "RUNNING"
            message = ""

            while True:
                # 双保险：轮次上限。
                if await self._loops_exhausted(run_id):
                    outcome = "MAX_LOOPS"
                    message = "轮次上限已耗尽"
                    break
                # 双保险：单轮超时。
                if self._clock.monotonic() - started > loop_timeout_s:
                    outcome = "TIMEOUT"
                    message = "单轮执行超时，安全收敛到 PAUSED"
                    await self._pause_run(run_id)
                    break

                await self._emit_loop_started(run_id, task_id, loop_index)
                ctx = PhaseContext(
                    run_id=run_id,
                    task_id=task_id,
                    loop_index=loop_index,
                    lease=lease,
                    provider=self._deps.provider,
                    executor=self._deps.executor,
                    tool_registry=self._deps.tool_registry,
                    tool_dispatcher=self._deps.tool_dispatcher,
                    context_manager=self._deps.context_manager,
                    budget_guard=self._deps.budget_guard,
                    policy_engine=self._deps.policy_engine,
                    idempotency=self._deps.idempotency,
                    lease_manager=self._deps.lease_manager,
                    checkpoint=self._deps.checkpoint,
                    verification=self._deps.verification,
                    settings=self._deps.settings,
                    workspace=self._deps.workspace,
                    clock=self._clock,
                    state={},
                )

                round_outcome, round_message = await self._run_phases(ctx, phase_results)
                if round_outcome in ("BUDGET_HALTED",):
                    outcome = "BUDGET_HALTED"
                    message = round_message
                    await self._halt_run(run_id)
                    break
                if round_outcome in ("VERIFY_FAILED", "COMMIT_FAILED"):
                    outcome = round_outcome
                    message = round_message
                    # 失败回流：不中断整个 run_loop，进入下一轮重试。
                    outcome = outcome  # 保留最后一次失败原因

                if ctx.state.get("commit_sha"):
                    outcome = "COMMITTED"
                    message = f"已提交 {ctx.state.get('commit_sha')}"

                # 写轮次检查点。
                if self._deps.settings.orchestration.checkpoint_every_loop:
                    await self._deps.checkpoint.save(
                        run_id=run_id,
                        task_id=task_id,
                        loop_index=loop_index,
                        kind="loop",
                        snapshot={
                            "outcome": outcome,
                            "commit_sha": ctx.state.get("commit_sha", ""),
                            "phase_count": len(phase_results),
                        },
                        last_step_id=None,
                        last_event_seq=0,
                    )

                loop_index = await self._advance_loop(run_id)
                if loop_index < 0:
                    break

            # 收尾：轮次耗尽且任务终态 → SUCCEEDED。
            final_state = await self._finalize(run_id, outcome)
            if final_state is not None:
                outcome = final_state

            duration_ms = int((self._clock.monotonic() - started) * 1000)
            await bus.drain(timeout_s=2.0)
            run_events = [
                e for e in counter.events if e.run_id == run_id
            ]
            return LoopOutcome(
                run_id=run_id,
                task_id=task_id,
                loop_index=max(0, loop_index),
                outcome=outcome,
                message=message,
                phase_results=[r.model_dump() for r in phase_results],
                steps_created=sum(r.steps_created for r in phase_results),
                events_published=len(run_events),
                commit_sha=(
                    next(
                        (
                            r.data.get("commit_sha", "")
                            for r in phase_results
                            if r.phase == "COMMIT" and r.data.get("commit_sha")
                        ),
                        "",
                    )
                ),
                branch=(
                    next(
                        (
                            r.data.get("branch", "")
                            for r in phase_results
                            if r.phase == "COMMIT" and r.data.get("branch")
                        ),
                        "",
                    )
                ),
                duration_ms=duration_ms,
            )
        finally:
            bus.unsubscribe(sub_id)

    # -- 内部 ---------------------------------------------------------------

    async def _run_phases(
        self, ctx: PhaseContext, phase_results: list[PhaseResult]
    ) -> tuple[str, str]:
        """顺序执行六阶段；返回 (outcome, message)。"""
        for phase in self._phases:
            result = await phase.run(ctx)
            phase_results.append(result)
            if not result.ok:
                if result.outcome == "BUDGET_HALTED":
                    return "BUDGET_HALTED", result.message
                if result.outcome == "VERIFY_FAILED":
                    return "VERIFY_FAILED", result.message
                if result.outcome in ("COMMIT_FAILED", "GIT_ADD_FAILED", "COMMIT_INTENT_FAILED"):
                    return "COMMIT_FAILED", result.message
                # 其他阶段失败：记录但不中断，下一轮重试。
        return "OK", ""

    async def _start_run(self, run_id: str, task_id: str, lease: Lease) -> None:
        """把 Run 置 RUNNING、Task 置 ACTIVE。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            await uow.runs.cas_state(
                run_id,
                from_state=[
                    RunState.CREATED,
                    RunState.PLANNING,
                    RunState.READY,
                    RunState.PAUSED,
                    RunState.RECOVERING,
                    RunState.RECONCILING,
                    RunState.HALTED,
                ],
                to_state=RunState.RUNNING,
            )
            task = await uow.tasks.get(task_id)
            if task is not None and task.state is TaskState.LEASED:
                await uow.tasks.cas_transition(
                    task_id,
                    from_state=TaskState.LEASED,
                    to_state=TaskState.ACTIVE,
                    expected_version=task.version,
                )
                uow.collect_event(
                    EventType.TASK_ACTIVATED,
                    {
                        "run_id": run_id,
                        "task_id": task_id,
                        "task_key": task.task_key,
                        "state": TaskState.ACTIVE,
                        "previous_state": TaskState.LEASED,
                        "reason": f"lease={lease.id}",
                    },
                    run_id=run_id,
                    task_id=task_id,
                )
            await uow.commit()

    async def _emit_loop_started(
        self, run_id: str, task_id: str, loop_index: int
    ) -> None:
        """发射 LOOP_STARTED。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            uow.collect_event(
                EventType.LOOP_STARTED,
                {
                    "run_id": run_id,
                    "task_id": task_id,
                    "loop_index": loop_index,
                    "phase": "PLAN",
                    "outcome": "started",
                },
                run_id=run_id,
                task_id=task_id,
            )
            await uow.commit()

    async def _current_loop(self, run_id: str) -> int:
        """读取当前轮次。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            run = await uow.runs.get(run_id)
            return run.loop_index if run is not None else 0

    async def _loops_exhausted(self, run_id: str) -> bool:
        """轮次是否已耗尽。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            return await uow.runs.is_exhausted(run_id)

    async def _advance_loop(self, run_id: str) -> int:
        """loop_index +1。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            return await uow.runs.advance_loop(run_id)

    async def _pause_run(self, run_id: str) -> None:
        """超时安全收敛到 PAUSED。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            await uow.runs.cas_state(
                run_id,
                from_state=[RunState.RUNNING, RunState.PAUSING],
                to_state=RunState.PAUSED,
            )
            await uow.commit()

    async def _halt_run(self, run_id: str) -> None:
        """预算 halt → HALTED。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            await uow.runs.cas_state(
                run_id,
                from_state=[RunState.RUNNING],
                to_state=RunState.HALTED,
                extra={"error_code": "LF-BUDGET-1101"},
            )
            await uow.commit()

    async def _finalize(self, run_id: str, outcome: str) -> str | None:
        """收尾判定：全部任务终态 → SUCCEEDED；轮次耗尽 → FAILED。

        :returns: 若状态发生变化返回新状态字符串；否则 ``None``。
        """
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            run = await uow.runs.get(run_id)
            if run is None:
                return None
            if await uow.tasks.all_terminal(run_id):
                await uow.runs.cas_state(
                    run_id,
                    from_state=[RunState.RUNNING],
                    to_state=RunState.SUCCEEDED,
                )
                uow.collect_event(
                    EventType.RUN_SUCCEEDED,
                    {
                        "run_id": run_id,
                        "state": RunState.SUCCEEDED,
                        "previous_state": RunState.RUNNING,
                        "reason": "all_tasks_terminal",
                    },
                    run_id=run_id,
                )
                await uow.commit()
                return RunState.SUCCEEDED.value
            if await uow.runs.is_exhausted(run_id):
                await uow.runs.cas_state(
                    run_id,
                    from_state=[RunState.RUNNING],
                    to_state=RunState.FAILED,
                    extra={"error_code": "LF-RUN-0309"},
                )
                uow.collect_event(
                    EventType.RUN_FAILED,
                    {
                        "run_id": run_id,
                        "error_code": "LF-RUN-0309",
                        "message": "轮次上限耗尽",
                        "loop_index": run.loop_index,
                        "recoverable": False,
                    },
                    run_id=run_id,
                    severity=Severity.ERROR,
                )
                await uow.commit()
                return RunState.FAILED.value
        return None
