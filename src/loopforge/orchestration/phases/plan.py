"""PLAN 阶段 —— 构建上下文 → 预算预检 → Planner 出计划与 tool_calls。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from loopforge.core.enums import RoleName
from loopforge.core.event_types import EventType
from loopforge.db.uow import UnitOfWork
from loopforge.orchestration.phases.base import BasePhase, PhaseContext, PhaseResult
from loopforge.providers.types import ChatRequest

__all__ = ["PlanPhase"]


class PlanPhase(BasePhase):
    """计划阶段：产出计划文本与候选工具调用。"""

    name = "PLAN"

    async def _execute(self, ctx: PhaseContext) -> PhaseResult:
        # 1. 构建上下文。
        pack = await ctx.context_manager.build_context(
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            role=RoleName.PLANNER,
            token_budget=32_000,
        )

        # 2. 预算预检。
        if ctx.budget_guard is not None:
            verdict = await ctx.budget_guard.precheck(
                run_id=ctx.run_id, estimated_usd=Decimal("0.01")
            )
            if verdict.action in ("HALT", "THROTTLE") and not getattr(
                verdict, "allowed", True
            ):
                return PhaseResult(
                    phase=self.name,
                    loop_index=ctx.loop_index,
                    ok=False,
                    outcome="BUDGET_HALTED",
                    message=f"预算预检未通过：{verdict.action}",
                )

        # 3. Planner 出计划。
        task = await self._load_task(ctx)
        user_text = f"任务：{task.title}\n{task.body}" if task else ctx.task_id
        specs = ctx.tool_registry.to_specs(RoleName.PLANNER)
        request = ChatRequest(
            model="mock-large",
            messages=pack.to_messages() + [
                {"role": "user", "content": user_text},
            ],
            tools=specs,
            tool_choice="auto",
            temperature=0.2,
            seed=42,
            metadata={
                "role": RoleName.PLANNER.value,
                "loop_index": str(ctx.loop_index),
                "task_key": task.task_key if task else "",
                "context_digest": "plan",
            },
        )
        result = await ctx.provider.chat(request)

        # 4. 记录 LLM_CALL 事件。
        async with UnitOfWork(session_factory=None) as uow:
            uow.collect_event(
                EventType.LLM_CALL,
                {
                    "run_id": ctx.run_id,
                    "task_id": ctx.task_id,
                    "provider": result.model,
                    "model": result.model,
                    "role": RoleName.PLANNER,
                    "prompt_tokens": result.usage.prompt_tokens,
                    "completion_tokens": result.usage.completion_tokens,
                    "cost_usd": "0",
                    "latency_ms": result.latency_ms,
                    "finish_reason": result.finish_reason,
                },
                run_id=ctx.run_id,
                task_id=ctx.task_id,
            )
            await uow.commit()

        # 5. 共享状态：计划文本 + 候选工具调用。
        ctx.state["plan"] = result.content
        ctx.state["plan_tool_calls"] = [
            {
                "id": call.id,
                "name": call.name,
                "arguments": dict(call.arguments or {}),
            }
            for call in result.tool_calls
        ]
        ctx.state["_phase"] = self.name
        return PhaseResult(
            phase=self.name,
            loop_index=ctx.loop_index,
            ok=True,
            outcome="PLANNED",
            steps_created=0,
            data={
                "plan_length": len(result.content),
                "tool_calls": len(result.tool_calls),
                "prompt_tokens": result.usage.prompt_tokens,
            },
        )

    async def _load_task(self, ctx: PhaseContext) -> Any:
        """读取 Task（用于给 Planner 拼任务描述）。"""
        from loopforge.db.uow import UnitOfWork

        async with UnitOfWork() as uow:
            return await uow.tasks.get(ctx.task_id)
