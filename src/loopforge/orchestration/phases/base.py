"""阶段基类 —— 统一事件发射、异常包装、检查点写入。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1。

每个 Phase 的约定
-----------------
1. 继承 :class:`BasePhase`，实现 ``_execute(ctx) -> PhaseResult``；
2. ``run()`` 由基类统一包装：计时、异常归一化、写 ``step`` 粒度检查点；
3. 阶段内所有 DB 写走 ``UnitOfWork``，事件用 ``uow.collect_event`` 收集，
   commit 成功后才发布（事务边界 = 事件边界）；
4. 阶段之间通过 :class:`PhaseContext.state` 传递共享状态
   （plan 产出 tool_calls → act 消费 → observe 汇总 → verify 校验 → commit 提交）。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.clock import Clock, get_clock
from loopforge.core.errors import LoopForgeError, wrap_exception
from loopforge.orchestration.checkpoint import CheckpointStore
from loopforge.orchestration.idempotency import IdempotencyGuard
from loopforge.orchestration.lease_manager import LeaseManager
from loopforge.models.lease import Lease

__all__ = ["PhaseContext", "PhaseResult", "BasePhase"]


@dataclass(slots=True)
class PhaseContext:
    """一次循环中单个阶段共享的上下文。

    Attributes:
        run_id / task_id / loop_index: 循环坐标。
        lease: 当前租约。
        provider / executor: 模型与执行器。
        tool_dispatcher / tool_registry: 工具。
        context_manager: 上下文管理器（WP5）。
        budget_guard: 预算守卫（WP5）；可为 ``None``（跳过预检）。
        idempotency / lease_manager / checkpoint: WP4 内部服务。
        verification: 验证流水线（WP3）。
        settings: 全局配置。
        workspace: 工作区句柄。
        clock: 时间源。
        state: 阶段间共享状态字典。
    """

    run_id: str
    task_id: str
    loop_index: int
    lease: Lease | None
    provider: Any
    executor: Any
    tool_registry: Any
    tool_dispatcher: Any
    context_manager: Any
    budget_guard: Any
    policy_engine: Any
    idempotency: IdempotencyGuard
    lease_manager: LeaseManager
    checkpoint: CheckpointStore
    verification: Any
    settings: Any
    workspace: Any
    clock: Clock | None = None
    state: dict[str, Any] = field(default_factory=dict)


class PhaseResult(BaseModel):
    """一个阶段的结果。"""

    model_config = ConfigDict(extra="forbid")

    phase: str
    loop_index: int = 0
    ok: bool = True
    outcome: str = ""
    steps_created: int = 0
    events_emitted: int = 0
    error_code: str = ""
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0


class BasePhase(abc.ABC):
    """阶段基类。

    :param name: 阶段名（PLAN/ACT/OBSERVE/VERIFY/COMMIT/EVOLVE）。
    :param clock: 时间源；``None`` 使用全局。
    """

    name: str = "PHASE"

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock or get_clock()

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        """统一入口：计时 → 执行 → 异常归一化 → 写检查点。

        :param ctx: 阶段上下文。
        :returns: 阶段结果。
        """
        started = self._clock.monotonic()
        try:
            result = await self._execute(ctx)
        except LoopForgeError as exc:
            result = PhaseResult(
                phase=self.name,
                loop_index=ctx.loop_index,
                ok=False,
                outcome="PHASE_ERROR",
                error_code=exc.code,
                message=exc.message,
                data={"details": exc.details},
            )
        except Exception as exc:  # noqa: BLE001 - 未知异常归一化
            wrapped = wrap_exception(exc, message=f"{self.name} 阶段异常")
            result = PhaseResult(
                phase=self.name,
                loop_index=ctx.loop_index,
                ok=False,
                outcome="PHASE_ERROR",
                error_code=wrapped.code,
                message=wrapped.message,
                data={"details": wrapped.details},
            )
        result.duration_ms = int((self._clock.monotonic() - started) * 1000)
        try:
            await self._checkpoint(ctx, result)
        except Exception:  # noqa: BLE001 - 检查点失败不影响阶段结果
            pass
        return result

    # -- 子类实现 -----------------------------------------------------------

    @abc.abstractmethod
    async def _execute(self, ctx: PhaseContext) -> PhaseResult:
        """执行阶段逻辑。"""
        raise NotImplementedError

    # -- 基础设施 -----------------------------------------------------------

    async def _checkpoint(self, ctx: PhaseContext, result: PhaseResult) -> None:
        """每个阶段结束写一个 ``step`` 粒度检查点。"""
        if not ctx.settings.orchestration.checkpoint_every_step:
            return
        await ctx.checkpoint.save(
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            loop_index=ctx.loop_index,
            kind="step",
            snapshot={
                "phase": self.name,
                "ok": result.ok,
                "outcome": result.outcome,
                "error_code": result.error_code,
                "state_keys": sorted(ctx.state.keys()),
            },
            last_step_id=result.data.get("last_step_id"),
            last_event_seq=0,
        )

    @staticmethod
    def _ok(ctx: PhaseContext, *, outcome: str = "", **data: Any) -> PhaseResult:
        """便捷构造成功结果。"""
        return PhaseResult(
            phase="PHASE",
            loop_index=ctx.loop_index,
            ok=True,
            outcome=outcome,
            data=dict(data),
        )
