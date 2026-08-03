"""VERIFY 阶段 —— 调 VerificationPipeline，失败生成修复子任务回流。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1。

Mock 模式下验证流水线对空仓库 / 简单文件直接通过；
失败时由 LoopEngine 决定生成修复子任务（回流）。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.event_types import EventType
from loopforge.db.uow import UnitOfWork
from loopforge.orchestration.phases.base import BasePhase, PhaseContext, PhaseResult

__all__ = ["VerifyPhase"]


class VerifyPhase(BasePhase):
    """验证阶段：运行验证流水线并记录结果。"""

    name = "VERIFY"

    async def _execute(self, ctx: PhaseContext) -> PhaseResult:
        async with UnitOfWork() as uow:
            uow.collect_event(
                EventType.VERIFY_STARTED,
                {
                    "run_id": ctx.run_id,
                    "task_id": ctx.task_id,
                    "steps": ["build", "unit", "e2e", "lifecycle"],
                    "commit_sha": ctx.state.get("commit_sha", ""),
                },
                run_id=ctx.run_id,
                task_id=ctx.task_id,
            )
            await uow.commit()

        report = await ctx.verification.run(
            ctx.executor, ctx.workspace, timeout_s=120.0
        )

        async with UnitOfWork() as uow:
            uow.collect_event(
                EventType.VERIFY_RESULT if report.ok else EventType.VERIFY_FAILED,
                {
                    "run_id": ctx.run_id,
                    "task_id": ctx.task_id,
                    "step": "pipeline",
                    "passed": report.ok,
                    "duration_ms": report.duration_ms,
                    "error_code": (
                        report.blocking_failure.error_code if report.blocking_failure else ""
                    ),
                    "detail": report.summary[:1000],
                },
                run_id=ctx.run_id,
                task_id=ctx.task_id,
            )
            await uow.commit()

        ctx.state["verify_report"] = report.to_dict() if hasattr(report, "to_dict") else {}
        ctx.state["_phase"] = self.name
        return PhaseResult(
            phase=self.name,
            loop_index=ctx.loop_index,
            ok=report.ok,
            outcome="VERIFIED" if report.ok else "VERIFY_FAILED",
            data={
                "passed": report.ok,
                "duration_ms": report.duration_ms,
                "stages": len(report.results),
            },
        )
