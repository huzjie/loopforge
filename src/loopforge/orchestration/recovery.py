"""恢复协调器 —— 扫描未确认 Step → probe 对账 → skip/replay/replan/ask_human。

来源：``docs/ARCHITECTURE.md`` §8.1.4、``docs/TASK_BREAKDOWN.md`` §6.1.1 / §11.6。

对账决策表（§8.1.4）
--------------------
| side_effect_class | 处理                                    |
|-------------------|-----------------------------------------|
| PURE              | 直接重放（读操作，无副作用）            |
| IDEMPOTENT        | 直接重放（同内容写入 = no-op）          |
| PROBEABLE         | probe()：executed=True → CONFIRM；否则重放 |
| UNSAFE            | 不重放 → UNCERTAIN，按 on_uncertain 策略 |

``on_uncertain`` 三种策略：``replan``（默认）/ ``ask_human`` / ``fail``。

冻结契约（§11.6）::

    class RecoveryCoordinator:
        async def reconcile(self, run_id: str) -> ReconcileReport: ...
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.clock import get_clock
from loopforge.core.config import RecoverySettings, get_settings
from loopforge.core.enums import (
    MemoryLayer,
    RoleName,
    RunState,
    Severity,
    SideEffectClass,
    StepState,
    TaskState,
)
from loopforge.core.event_types import EventType
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork
from loopforge.models.step import Step
from loopforge.orchestration.idempotency import IdempotencyGuard, IntentTicket
from loopforge.tools.base import ProbeResult, StepIntent, ToolResult
from loopforge.tools.dispatcher import ToolDispatcher
from loopforge.tools.registry import ToolRegistry

__all__ = ["ReconcileReport", "RecoveryCoordinator"]


class ReconcileReport(BaseModel):
    """一次对账的结果。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    scanned: int = 0
    replayed: int = 0
    probed: int = 0
    confirmed: int = 0
    uncertain: int = 0
    skipped: int = 0
    strategy: str = "replan"
    recovery_count: int = 0
    run_state: str = ""


class RecoveryCoordinator:
    """崩溃恢复协调器（冻结契约 §11.6）。

    :param session_factory: session 工厂；``None`` 使用全局。
    :param settings: 恢复配置；``None`` 使用全局。
    :param idempotency: 幂等守卫；``None`` 自动创建。
    :param tool_registry: 工具注册表；``None`` 自动加载内置工具。
    :param tool_dispatcher: 工具分发器；``None`` 基于注册表创建。
    :param executor: 执行器（工具执行所需）；``None`` 时只做探测与标记。
    """

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        settings: RecoverySettings | None = None,
        idempotency: IdempotencyGuard | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_dispatcher: ToolDispatcher | None = None,
        executor: Any | None = None,
    ) -> None:
        self._session_factory: Any = (
            session_factory if session_factory is not None else get_session_factory()
        )
        self._settings: RecoverySettings = (
            settings if settings is not None else get_settings().recovery
        )
        self._idem: IdempotencyGuard = idempotency or IdempotencyGuard(
            session_factory=self._session_factory
        )
        self._registry: ToolRegistry = tool_registry or ToolRegistry()
        if not tool_registry:
            try:
                self._registry.autoload_builtins()
            except Exception:  # noqa: BLE001
                pass
        self._dispatcher: ToolDispatcher = tool_dispatcher or ToolDispatcher(
            self._registry
        )
        self._executor: Any = executor

    # -- 主入口 -------------------------------------------------------------

    async def reconcile(self, run_id: str) -> ReconcileReport:
        """对账一个 Run 的全部未确认 Step。

        :param run_id: 目标 Run。
        :returns: 对账报告。
        """
        unconfirmed = await self._idem.find_unconfirmed(run_id)
        report = ReconcileReport(
            run_id=run_id, scanned=len(unconfirmed), strategy=self._settings.on_uncertain
        )

        async with UnitOfWork(session_factory=self._session_factory) as uow:
            run = await uow.runs.get(run_id)
            if run is None:
                report.run_state = "missing"
                await uow.rollback()
                return report

            # 标记 Run 进入恢复态。
            await uow.runs.cas_state(
                run_id,
                from_state=[RunState.RUNNING, RunState.READY, RunState.RECOVERING],
                to_state=RunState.RECOVERING,
            )
            await uow.commit()

        for step in unconfirmed:
            ticket = IntentTicket(
                step_id=step.id,
                run_id=step.run_id,
                task_id=step.task_id,
                idempotency_key=step.idempotency_key,
                phase=step.phase,
                tool_name=str((step.intent_payload or {}).get("tool_name", "")),
                side_effect_class=step.side_effect_class,
                fence_token=step.fence_token,
                loop_index=step.loop_index,
                role=step.role,
                intent_payload=dict(step.intent_payload or {}),
            )
            await self._reconcile_step(step, ticket, report)

        # 恢复计数 + 状态收尾。
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            new_recovery = await uow.runs.increment_recovery(run_id)
            if report.uncertain > 0 and self._settings.on_uncertain == "ask_human":
                await uow.runs.cas_state(
                    run_id,
                    from_state=[RunState.RECOVERING, RunState.RECONCILING],
                    to_state=RunState.AWAITING_APPROVAL,
                )
                report.run_state = RunState.AWAITING_APPROVAL.value
            elif report.uncertain > 0 and self._settings.on_uncertain == "fail":
                await uow.runs.cas_state(
                    run_id,
                    from_state=[RunState.RECOVERING, RunState.RECONCILING],
                    to_state=RunState.FAILED,
                    extra={"error_code": "LF-RUN-0311"},
                )
                report.run_state = RunState.FAILED.value
            else:
                await uow.runs.cas_state(
                    run_id,
                    from_state=[RunState.RECOVERING, RunState.RECONCILING],
                    to_state=RunState.RUNNING,
                )
                report.run_state = RunState.RUNNING.value
            report.recovery_count = new_recovery if new_recovery > 0 else 0
            uow.collect_event(
                EventType.RUN_RECOVERED,
                {
                    "run_id": run_id,
                    "recovery_count": report.recovery_count,
                    "unconfirmed_steps": report.scanned,
                    "strategy": self._settings.on_uncertain,
                },
                run_id=run_id,
            )
            await uow.commit()
        return report

    # -- 对账单步 -----------------------------------------------------------

    async def _reconcile_step(
        self, step: Step, ticket: IntentTicket, report: ReconcileReport
    ) -> None:
        """按副作用分类处理一条未确认 Step。"""
        cls = step.side_effect_class
        intent = StepIntent(
            run_id=step.run_id,
            task_id=step.task_id or "",
            step_id=step.id,
            idempotency_key=step.idempotency_key,
            tool_name=ticket.tool_name,
            args=dict((step.intent_payload or {}).get("args", {})),
            expected_sha256=None,
            expected_external_ref=step.external_ref,
            context={
                "loop_index": step.loop_index,
                "phase": step.phase,
                "role": step.role,
            },
        )

        if cls is SideEffectClass.PURE:
            # 读操作：直接确认（重放无副作用，等价于记录成功）。
            await self._confirm_with_note(step, "replayed_as_pure")
            report.replayed += 1
            report.confirmed += 1
            return

        if cls is SideEffectClass.IDEMPOTENT:
            # 同内容写入 = no-op：重放安全。
            result = await self._replay_tool(step, intent)
            if result is not None and result.ok:
                await self._idem.confirm(ticket, result)
                report.replayed += 1
                report.confirmed += 1
            else:
                await self._mark_uncertain(step, "idempotent_replay_failed")
                report.uncertain += 1
            return

        if cls is SideEffectClass.PROBEABLE:
            probe = await self._probe_tool(step, intent)
            report.probed += 1
            if probe is not None and probe.executed:
                # 副作用已存在：直接确认并写回 external_ref。
                await self._confirm_probed(step, probe)
                report.confirmed += 1
                return
            # 未执行：重放。
            result = await self._replay_tool(step, intent)
            if result is not None and result.ok:
                await self._idem.confirm(ticket, result)
                report.replayed += 1
                report.confirmed += 1
            else:
                await self._mark_uncertain(step, "probeable_replay_failed")
                report.uncertain += 1
            return

        # UNSAFE：不重放。
        await self._mark_uncertain(step, "unsafe_no_replay")
        report.uncertain += 1
        await self._apply_uncertain_strategy(step)

    # -- 内部工具 -----------------------------------------------------------

    async def _probe_tool(self, step: Step, intent: StepIntent) -> ProbeResult | None:
        """调用工具 probe()。"""
        try:
            ctx = self._build_ctx(step, intent)
            return await self._dispatcher.probe_tool(ctx, intent.tool_name, intent)
        except Exception:  # noqa: BLE001 - 探测失败不应中断对账
            return None

    async def _replay_tool(self, step: Step, intent: StepIntent) -> ToolResult | None:
        """重放工具（不新建 Step —— 复用原 Step 的 key 与上下文）。"""
        if self._executor is None:
            return None
        try:
            ctx = self._build_ctx(step, intent)
            return await self._dispatcher.dispatch(ctx, intent.tool_name, intent.args)
        except Exception:  # noqa: BLE001 - 重放失败标记不确定
            return None

    def _build_ctx(self, step: Step, intent: StepIntent) -> Any:
        """构造工具上下文。"""
        from loopforge.tools.base import ToolContext

        return ToolContext(
            run_id=step.run_id,
            task_id=step.task_id or "",
            step_id=step.id,
            trace_id="",
            workspace=None,
            executor=self._executor,
            vcs=None,
            policy=None,
            idempotency_key=step.idempotency_key,
            fence_token=step.fence_token,
        )

    async def _confirm_with_note(self, step: Step, note: str) -> None:
        """直接把 Step 置 CONFIRMED（PURE 重放用）。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            await uow.steps.confirm(
                step.id,
                result_payload={"reconcile": note, "replayed": True},
            )
            uow.collect_event(
                EventType.STEP_RECONCILED,
                {
                    "run_id": step.run_id,
                    "task_id": step.task_id or "",
                    "step_id": step.id,
                    "idempotency_key": step.idempotency_key,
                    "probe_result": "not_applicable",
                    "decision": "replay_pure",
                },
                run_id=step.run_id,
                task_id=step.task_id,
                step_id=step.id,
            )
            await uow.commit()

    async def _confirm_probed(self, step: Step, probe: ProbeResult) -> None:
        """探测到副作用已存在：确认并写回 external_ref。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            await uow.steps.confirm(
                step.id,
                result_payload={"reconcile": "probe_executed", "evidence": probe.evidence},
                external_ref=probe.external_ref,
            )
            uow.collect_event(
                EventType.STEP_RECONCILED,
                {
                    "run_id": step.run_id,
                    "task_id": step.task_id or "",
                    "step_id": step.id,
                    "idempotency_key": step.idempotency_key,
                    "probe_result": "executed",
                    "decision": "confirm_already_executed",
                    "external_ref": probe.external_ref or "",
                },
                run_id=step.run_id,
                task_id=step.task_id,
                step_id=step.id,
            )
            await uow.commit()

    async def _mark_uncertain(self, step: Step, reason: str) -> None:
        """标记 UNCERTAIN 并发射事件。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            await uow.steps.mark_uncertain(step.id, reason=reason)
            uow.collect_event(
                EventType.STEP_UNCERTAIN,
                {
                    "run_id": step.run_id,
                    "task_id": step.task_id or "",
                    "step_id": step.id,
                    "idempotency_key": step.idempotency_key,
                    "probe_result": "unknown",
                    "decision": self._settings.on_uncertain,
                },
                run_id=step.run_id,
                task_id=step.task_id,
                step_id=step.id,
                severity="ERROR",
            )
            await uow.commit()

    async def _apply_uncertain_strategy(self, step: Step) -> None:
        """UNSAFE 不确定副作用的后续处理。"""
        if self._settings.on_uncertain == "replan":
            # 把「意图 + 结果未知 + 现状」写入记忆，让 LLM 重新规划。
            try:
                async with UnitOfWork(session_factory=self._session_factory) as uow:
                    await uow.memory.add_item(
                        run_id=step.run_id,
                        task_id=step.task_id,
                        layer=MemoryLayer.EPISODIC,
                        content=(
                            f"步骤 {step.id}（{step.phase}）意图为 "
                            f"{step.intent_payload}，崩溃后结果未知，需要重新规划。"
                        ),
                        salience=0.8,
                    )
                    await uow.commit()
            except Exception:  # noqa: BLE001
                pass
        elif self._settings.on_uncertain == "ask_human":
            # 创建审批（模拟 —— 真实审批走 governance 包）。
            try:
                async with UnitOfWork(session_factory=self._session_factory) as uow:
                    uow.collect_event(
                        EventType.APPROVAL_REQUESTED,
                        {
                            "approval_id": step.id,
                            "run_id": step.run_id,
                            "task_id": step.task_id,
                            "step_id": step.id,
                            "action_kind": "reconcile_uncertain",
                            "state": "PENDING",
                            "timeout_action": self._settings.on_uncertain,
                        },
                        run_id=step.run_id,
                        task_id=step.task_id,
                        step_id=step.id,
                        severity=Severity.NOTICE,
                    )
                    await uow.commit()
            except Exception:  # noqa: BLE001
                pass
        # "fail" 策略：不在这里立即置 Task FAILED —— 由 reconcile 收尾统一处理。
