"""OBSERVE 阶段 —— 结果入工作记忆、变更汇总、进展判定、压缩触发判定。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1 / §8.2。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.enums import MemoryLayer
from loopforge.core.event_types import EventType
from loopforge.db.uow import UnitOfWork
from loopforge.orchestration.phases.base import BasePhase, PhaseContext, PhaseResult

__all__ = ["ObservePhase"]


class ObservePhase(BasePhase):
    """观察阶段：把 ACT 的结果沉淀进记忆并汇总变更。"""

    name = "OBSERVE"

    async def _execute(self, ctx: PhaseContext) -> PhaseResult:
        results = list(ctx.state.get("act_results", []) or [])
        changed_paths: list[str] = []

        # 1. 逐条写入工作记忆。
        for item in results:
            step_id = item.get("step_id")
            tool_name = item.get("tool_name", "")
            outcome = item.get("outcome", "unknown")
            if not step_id:
                continue
            content = (
                f"步骤 {step_id}：工具 {tool_name} 结果 {outcome}"
            )
            try:
                memory_item = await ctx.context_manager.append_observation(
                    step_id=step_id,
                    content=content,
                    layer=MemoryLayer.WORKING,
                )
                async with UnitOfWork() as uow:
                    uow.collect_event(
                        EventType.MEMORY_APPENDED,
                        {
                            "run_id": ctx.run_id,
                            "item_id": memory_item.id,
                            "layer": MemoryLayer.WORKING,
                            "tokens": memory_item.tokens,
                            "salience": memory_item.salience,
                            "task_id": ctx.task_id,
                        },
                        run_id=ctx.run_id,
                        task_id=ctx.task_id,
                        step_id=step_id,
                    )
                    await uow.commit()
            except Exception:  # noqa: BLE001 - 记忆写入失败不阻断观察
                pass

        # 2. 变更汇总：收集 ACT 结果的路径信息。
        for item in results:
            path = item.get("path")
            if path:
                changed_paths.append(str(path))
        ctx.state["changed_paths"] = changed_paths
        ctx.state["_phase"] = self.name

        # 3. 进展判定：交给 LoopEngine 的 ProgressDetector 处理（本阶段只汇总）。
        ctx.state["progress_note"] = f"changed_files={len(changed_paths)}"

        # 4. 压缩触发判定（每 10 轮尝试一次，失败静默）。
        if ctx.loop_index > 0 and ctx.loop_index % 10 == 0:
            try:
                await ctx.context_manager.maybe_compress(
                    run_id=ctx.run_id, reason="token_threshold"
                )
            except Exception:  # noqa: BLE001
                pass

        return PhaseResult(
            phase=self.name,
            loop_index=ctx.loop_index,
            ok=True,
            outcome="OBSERVED",
            steps_created=0,
            data={"changed_files": len(changed_paths), "observations": len(results)},
        )
