"""EVOLVE 阶段 —— 经验沉淀为语义记忆；失败归因投递 EvolutionIntake。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.enums import MemoryLayer, RoleName
from loopforge.core.event_types import EventType
from loopforge.db.uow import UnitOfWork
from loopforge.orchestration.phases.base import BasePhase, PhaseContext, PhaseResult

__all__ = ["EvolvePhase"]


class EvolvePhase(BasePhase):
    """演化阶段：把本轮经验沉淀进语义记忆。"""

    name = "EVOLVE"

    async def _execute(self, ctx: PhaseContext) -> PhaseResult:
        commit_sha = ctx.state.get("commit_sha", "")
        plan = str(ctx.state.get("plan", ""))[:500]
        act_results = list(ctx.state.get("act_results", []) or [])
        verify_ok = ctx.state.get("verify_report", {}).get("ok", True)

        summary_lines = [
            f"Run {ctx.run_id} / Task {ctx.task_id} / loop {ctx.loop_index}",
            f"commit: {commit_sha or '无'}",
            f"plan: {plan or '（空）'}",
            f"acts: {len(act_results)} 个工具调用",
            f"verify: {'通过' if verify_ok else '失败'}",
        ]
        content = "\n".join(summary_lines)

        # 1. 沉淀语义记忆。
        memory_item_id: str | None = None
        try:
            item = await ctx.context_manager.append_observation(
                step_id="",
                content=content,
                layer=MemoryLayer.SEMANTIC,
            )
            memory_item_id = item.id
        except Exception:  # noqa: BLE001
            pass

        # 2. 失败归因：VERIFY 失败 → 写 EPISODIC 记忆 + 演化事件。
        failure_note = ""
        if not verify_ok:
            failure_note = "验证失败，需要生成修复子任务回流。"
            try:
                await ctx.context_manager.append_observation(
                    step_id="",
                    content=(
                        f"Run {ctx.run_id} 第 {ctx.loop_index} 轮验证失败："
                        f"{ctx.state.get('verify_report', {}).get('summary', '')[:300]}"
                    ),
                    layer=MemoryLayer.EPISODIC,
                )
            except Exception:  # noqa: BLE001
                pass

        # 3. 发射演化事件（EVOLUTION_ITEM_CREATED）。
        async with UnitOfWork() as uow:
            uow.collect_event(
                EventType.EVOLUTION_ITEM_CREATED,
                {
                    "item_id": memory_item_id or f"evolve-{ctx.loop_index}",
                    "source": "loop",
                    "score": 0.5,
                    "cluster_id": ctx.task_id,
                    "title": f"loop-{ctx.loop_index}-{ctx.task_id[-8:]}",
                },
                run_id=ctx.run_id,
                task_id=ctx.task_id,
            )
            await uow.commit()

        ctx.state["_phase"] = self.name
        return PhaseResult(
            phase=self.name,
            loop_index=ctx.loop_index,
            ok=True,
            outcome="EVOLVED",
            data={
                "memory_item_id": memory_item_id,
                "failure_note": failure_note,
                "commit_sha": commit_sha,
            },
        )
