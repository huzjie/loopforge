"""幂等守卫 —— 幂等键 + 两阶段意图协议（崩溃恢复的基石）。

来源：``docs/ARCHITECTURE.md`` §8.1、``docs/TASK_BREAKDOWN.md`` §6.3.2 / §11.6。

三阶段协议
----------
::

    A. begin_intent()  → 写 steps 行（state=INTENT_RECORDED），独立事务提交
    B. 实际执行（工具调用，产生真实副作用）
    C. confirm()       → 写 result_payload + external_ref，置 CONFIRMED

崩溃窗口只存在于 B 与 C 之间：Step 停留在 ``INTENT_RECORDED``，
由 :class:`~loopforge.orchestration.recovery.RecoveryCoordinator` 按
``side_effect_class`` 决策表处理。

幂等键公式（§6.3.1 逐字冻结）：:

    idempotency_key = sha256(canonical_json({
        "run_id", "task_id", "loop_index", "phase", "tool_name", "args"
    })).hexdigest()[:32]

``canonical_json`` 必须来自 :mod:`loopforge.core.hashing` —— 键序、浮点格式、
Unicode 转义规则全仓唯一，否则同一动作会算出两个 key。
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import RoleName, SideEffectClass, StepState
from loopforge.core.errors import LoopForgeError
from loopforge.core.event_types import EventType
from loopforge.core.hashing import canonical_json, sha256_hex
from loopforge.core.ids import new_ulid
from loopforge.db.uow import UnitOfWork
from loopforge.models.step import Step
from loopforge.tools.base import StepIntent, ToolResult

__all__ = [
    "IntentTicket",
    "IdempotencyGuard",
    "normalize_args",
]


def normalize_args(args: Mapping[str, Any]) -> dict[str, Any]:
    """归一化工具参数 —— 保证同一动作在不同调用形态下键值一致。

    规则：仅做「可哈希化」归一化（交给 ``canonical_json`` 处理 Enum / Path /
    Decimal 等类型），并剔除明显的易变字段（时间戳类）以免破坏幂等键稳定。
    这里的实现保持最小：类型归一化由 :func:`canonical_json` 负责，
    本函数只负责复制与去键。

    :param args: 原始工具参数。
    :returns: 归一化后的参数字典。
    """
    normalized = dict(args)
    # 时间戳类字段参与哈希会让「同一动作」因时间不同而算出不同 key，
    # 恢复时无法匹配。这里剔除常见易变键（工具层约定不把它们作为副作用凭据）。
    for volatile in ("timestamp", "ts", "now", "started_at", "created_at"):
        normalized.pop(volatile, None)
    return normalized


class IntentTicket(BaseModel):
    """一次意图的票据 —— ``begin_intent`` 的返回值，``confirm`` / ``abort`` 的入参。

    Attributes:
        step_id: 已落库 Step 的主键。
        run_id: 所属 Run。
        task_id: 所属 Task。
        idempotency_key: 幂等键。
        phase: 阶段名（PLAN/ACT/OBSERVE/VERIFY/COMMIT/EVOLVE）。
        tool_name: 工具名。
        side_effect_class: 副作用等级。
        fence_token: 租约 fencing token。
        loop_index: 循环轮次。
        role: 角色。
        intent_payload: 脱敏后的意图内容（写回 Step 用）。
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str
    run_id: str
    task_id: str | None = None
    idempotency_key: str
    phase: str
    tool_name: str
    side_effect_class: SideEffectClass = SideEffectClass.PURE
    fence_token: int = 0
    loop_index: int = 0
    role: RoleName | None = None
    intent_payload: dict[str, Any] = Field(default_factory=dict)


class IdempotencyGuard:
    """两阶段意图协议的实现（冻结契约 §11.6）。

    :param session_factory: session 工厂；``None`` 使用全局。
    """

    def __init__(self, *, session_factory: Any | None = None) -> None:
        from loopforge.db.session import get_session_factory

        self._session_factory: Any = (
            session_factory if session_factory is not None else get_session_factory()
        )

    # -- 幂等键 -------------------------------------------------------------

    @staticmethod
    def compute_key(
        *,
        run_id: str,
        task_id: str,
        loop_index: int,
        phase: str,
        tool_name: str,
        args: Mapping[str, Any],
    ) -> str:
        """计算 Step 幂等键 —— ``sha256(canonical_json)[:32]``。

        :param run_id: Run ULID。
        :param task_id: Task ULID。
        :param loop_index: 循环轮次（从 0 开始）。
        :param phase: 阶段名。
        :param tool_name: 工具名。
        :param args: 归一化后的工具参数。
        :returns: 32 位小写十六进制幂等键。
        """
        payload = {
            "run_id": run_id,
            "task_id": task_id,
            "loop_index": loop_index,
            "phase": phase,
            "tool_name": tool_name,
            "args": normalize_args(args),
        }
        return sha256_hex(canonical_json(payload))[:32]

    # -- 三阶段 -------------------------------------------------------------

    async def begin_intent(self, intent: StepIntent) -> IntentTicket:
        """阶段 A：意图先落库并提交。

        :param intent: 意图（``step_id`` 可留空，由 DB 生成）。
        :returns: :class:`IntentTicket`。
        :raises IdempotencyConflictError: 幂等键已存在（并发重放被 DB 唯一索引挡住）。
        """
        from loopforge.core.errors import IdempotencyConflictError

        key = intent.idempotency_key or self.compute_key(
            run_id=intent.run_id,
            task_id=intent.task_id,
            loop_index=int(intent.context.get("loop_index", 0) or 0),
            phase=str(intent.context.get("phase", "ACT")),
            tool_name=intent.tool_name,
            args=intent.args,
        )
        phase = str(intent.context.get("phase", "ACT"))
        loop_index = int(intent.context.get("loop_index", 0) or 0)
        role = intent.context.get("role")
        role_enum = RoleName(role) if role else None
        side_effect_class = SideEffectClass(
            intent.context.get("side_effect_class", SideEffectClass.PURE.value)
        )
        fence_token = int(intent.context.get("fence_token", 0) or 0)
        attempt = int(intent.context.get("attempt", 0) or 0)

        async with UnitOfWork(session_factory=self._session_factory) as uow:
            try:
                step = await uow.steps.record_intent(
                    run_id=intent.run_id,
                    idempotency_key=key,
                    phase=phase,
                    task_id=intent.task_id,
                    loop_index=loop_index,
                    role=role_enum,
                    side_effect_class=side_effect_class,
                    intent_payload={"tool_name": intent.tool_name, "args": intent.args},
                    fence_token=fence_token,
                    attempt=attempt,
                )
            except IdempotencyConflictError:
                # 唯一索引兜底：说明该意图已记录过。读出已有 Step，
                # 返回其票据（调用方据此决定复用结果还是走恢复）。
                existing = await uow.steps.get_by_idempotency_key(key)
                if existing is None:
                    raise
                uow.collect_event(
                    EventType.STEP_INTENT_RECORDED,
                    {
                        "run_id": existing.run_id,
                        "task_id": existing.task_id or "",
                        "step_id": existing.id,
                        "idempotency_key": existing.idempotency_key,
                        "phase": existing.phase,
                        "role": existing.role,
                        "tool_name": intent.tool_name,
                        "side_effect_class": existing.side_effect_class.value,
                    },
                    run_id=existing.run_id,
                    task_id=existing.task_id,
                    step_id=existing.id,
                )
                await uow.commit()
                return self._ticket_from_step(existing, intent.tool_name)

            uow.collect_event(
                EventType.STEP_INTENT_RECORDED,
                {
                    "run_id": step.run_id,
                    "task_id": step.task_id or "",
                    "step_id": step.id,
                    "idempotency_key": step.idempotency_key,
                    "phase": step.phase,
                    "role": step.role,
                    "tool_name": intent.tool_name,
                    "side_effect_class": step.side_effect_class.value,
                },
                run_id=step.run_id,
                task_id=step.task_id,
                step_id=step.id,
            )
            await uow.commit()
            return self._ticket_from_step(step, intent.tool_name)

    async def confirm(self, ticket: IntentTicket, result: ToolResult) -> None:
        """阶段 C：副作用成功，写回结果并置 CONFIRMED。

        :param ticket: :meth:`begin_intent` 返回的票据。
        :param result: 工具执行结果。
        """
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            updated = await uow.steps.confirm(
                ticket.step_id,
                result_payload={
                    "ok": result.ok,
                    "output": result.output,
                    "data": result.data,
                },
                external_ref=result.external_ref,
            )
            if updated:
                uow.collect_event(
                    EventType.STEP_CONFIRMED,
                    {
                        "run_id": ticket.run_id,
                        "task_id": ticket.task_id or "",
                        "step_id": ticket.step_id,
                        "idempotency_key": ticket.idempotency_key,
                        "external_ref": result.external_ref or "",
                        "duration_ms": 0,
                    },
                    run_id=ticket.run_id,
                    task_id=ticket.task_id,
                    step_id=ticket.step_id,
                )
            await uow.commit()

    async def abort(self, ticket: IntentTicket, error: LoopForgeError) -> None:
        """阶段 C 的失败分支：置 FAILED 并记录错误码。

        :param ticket: :meth:`begin_intent` 返回的票据。
        :param error: 执行失败异常。
        """
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            updated = await uow.steps.mark_failed(
                ticket.step_id,
                error_code=error.code,
                result_payload={"message": error.message, "details": error.details},
            )
            if updated:
                uow.collect_event(
                    EventType.STEP_FAILED,
                    {
                        "run_id": ticket.run_id,
                        "task_id": ticket.task_id or "",
                        "step_id": ticket.step_id,
                        "idempotency_key": ticket.idempotency_key,
                        "error_code": error.code,
                        "message": error.message,
                        "attempt": 1,
                        "retryable": error.retryable,
                    },
                    run_id=ticket.run_id,
                    task_id=ticket.task_id,
                    step_id=ticket.step_id,
                    severity=error.http_status >= 500 and "ERROR" or "WARNING",
                )
            await uow.commit()

    async def find_unconfirmed(self, run_id: str) -> list[Step]:
        """列出某 Run 全部未确认 Step（恢复流程的输入）。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            return await uow.steps.list_unconfirmed(run_id=run_id)

    # -- 内部 ---------------------------------------------------------------

    @staticmethod
    def _ticket_from_step(step: Step, tool_name: str) -> IntentTicket:
        intent_payload = dict(step.intent_payload or {})
        return IntentTicket(
            step_id=step.id,
            run_id=step.run_id,
            task_id=step.task_id,
            idempotency_key=step.idempotency_key,
            phase=step.phase,
            tool_name=tool_name or str(intent_payload.get("tool_name", "")),
            side_effect_class=step.side_effect_class,
            fence_token=step.fence_token,
            loop_index=step.loop_index,
            role=step.role,
            intent_payload=intent_payload,
        )
