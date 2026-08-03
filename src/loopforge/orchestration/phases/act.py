"""ACT 阶段 —— 逐 tool_call：策略裁决 → 两阶段意图 → 执行 → 落库。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1 / §8.1.3。

核心是**两阶段意图协议**：

1. ``begin_intent`` 把意图落库（``INTENT_RECORDED``，独立事务提交）；
2. 执行工具（真实副作用）；
3. ``confirm`` / ``abort`` 写结果（与事件同一事务）。

崩溃发生在 2 与 3 之间时，Step 停留在 ``INTENT_RECORDED``，
由 RecoveryCoordinator 按 ``side_effect_class`` 处理。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.enums import PolicyDecision, RoleName
from loopforge.core.errors import LoopForgeError
from loopforge.core.event_types import EventType
from loopforge.db.uow import UnitOfWork
from loopforge.orchestration.idempotency import IntentTicket
from loopforge.orchestration.phases.base import BasePhase, PhaseContext, PhaseResult
from loopforge.tools.base import StepIntent, ToolContext

__all__ = ["ActPhase"]


class ActPhase(BasePhase):
    """执行阶段：把计划产出的工具调用真正执行并落库。"""

    name = "ACT"

    async def _execute(self, ctx: PhaseContext) -> PhaseResult:
        # 若计划阶段没有产出 tool_calls（纯文本计划），用 CODER 角色强制生成一个。
        tool_calls = list(ctx.state.get("plan_tool_calls", []) or [])
        if not tool_calls:
            tool_calls = await self._request_tool_call(ctx)

        executed = 0
        failed = 0
        last_step_id: str | None = None
        results: list[dict[str, Any]] = []
        for call in tool_calls:
            tool_name = call.get("name", "")
            args = dict(call.get("arguments", {}) or {})
            ticket, outcome = await self._execute_one(ctx, tool_name, args)
            if outcome == "confirmed":
                executed += 1
                last_step_id = ticket.step_id if ticket else last_step_id
            elif outcome == "failed":
                failed += 1
            results.append(
                {
                    "tool_name": tool_name,
                    "outcome": outcome,
                    "step_id": ticket.step_id if ticket else None,
                }
            )
        ctx.state["act_results"] = results
        ctx.state["_phase"] = self.name
        return PhaseResult(
            phase=self.name,
            loop_index=ctx.loop_index,
            ok=failed == 0,
            outcome="EXECUTED" if executed else ("FAILED" if failed else "NO_OP"),
            steps_created=executed + failed,
            data={
                "executed": executed,
                "failed": failed,
                "last_step_id": last_step_id,
                "results": results,
            },
        )

    # -- 单步执行 -----------------------------------------------------------

    async def _execute_one(
        self, ctx: PhaseContext, tool_name: str, args: dict[str, Any]
    ) -> tuple[IntentTicket | None, str]:
        """执行一个工具调用（两阶段意图协议）。"""
        tool = ctx.tool_registry.get_or_none(tool_name) if hasattr(
            ctx.tool_registry, "get_or_none"
        ) else None
        side_effect_class = getattr(tool, "side_effect_class", None)
        if side_effect_class is None:
            try:
                side_effect_class = ctx.tool_registry.get(tool_name).side_effect_class
            except Exception:  # noqa: BLE001
                side_effect_class = None

        # 1. 策略裁决。
        if ctx.policy_engine is not None:
            from loopforge.governance.policy.engine import PolicyAction

            action_kind = "file_write"
            if tool_name in ("git_ops", "create_pr"):
                action_kind = "git"
            elif tool_name in ("run_shell",):
                action_kind = "command"
            elif tool_name in ("http_fetch",):
                action_kind = "network"
            verdict = await ctx.policy_engine.evaluate(
                PolicyAction(
                    kind=action_kind,
                    tool_name=tool_name,
                    path=str(args.get("path", "")),
                    content_sample=str(args.get("content", ""))[:500],
                    argv=list(args.get("argv", []) or []),
                    branch=str(args.get("branch", "")) or None,
                    context={"run_id": ctx.run_id, "task_id": ctx.task_id},
                )
            )
            decision = getattr(verdict, "decision", PolicyDecision.ALLOW)
            if decision in (PolicyDecision.DENY, PolicyDecision.REQUIRE_APPROVAL):
                return None, "policy_denied"

        # 2. 幂等键。
        key = ctx.idempotency.compute_key(
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            loop_index=ctx.loop_index,
            phase="ACT",
            tool_name=tool_name,
            args=args,
        )

        # 3. begin_intent（意图先落库）。
        intent = StepIntent(
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            step_id="",
            idempotency_key=key,
            tool_name=tool_name,
            args=args,
            context={
                "loop_index": ctx.loop_index,
                "phase": "ACT",
                "role": RoleName.CODER,
                "side_effect_class": (
                    side_effect_class.value if side_effect_class is not None else "PURE"
                ),
                "fence_token": ctx.lease.fence_token if ctx.lease else 0,
            },
        )
        try:
            ticket = await ctx.idempotency.begin_intent(intent)
        except LoopForgeError as exc:
            return None, f"intent_failed:{exc.code}"

        # 4. 执行工具。
        tool_ctx = ToolContext(
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            step_id=ticket.step_id,
            trace_id="",
            workspace=ctx.workspace,
            executor=ctx.executor,
            vcs=None,
            policy=None,
            idempotency_key=ticket.idempotency_key,
            fence_token=ticket.fence_token,
        )
        result = await ctx.tool_dispatcher.dispatch(tool_ctx, tool_name, args)
        if result.ok:
            await ctx.idempotency.confirm(ticket, result)
            return ticket, "confirmed"
        error = LoopForgeError(
            result.output or "工具执行失败",
            details={"error_code": result.error_code, "data": result.data},
        )
        await ctx.idempotency.abort(ticket, error)
        return ticket, "failed"

    async def _request_tool_call(self, ctx: PhaseContext) -> list[dict[str, Any]]:
        """用 CODER 角色强制生成一个工具调用（Mock 全离线路径）。"""
        specs = ctx.tool_registry.to_specs(RoleName.CODER)
        from loopforge.providers.types import ChatRequest

        request = ChatRequest(
            model="mock-large",
            messages=[
                {"role": "system", "content": "你是实现工程师，请调用工具完成任务。"},
                {
                    "role": "user",
                    "content": (
                        f"请完成 Task {ctx.task_id}：{ctx.state.get('plan', '')[:500]}"
                    ),
                },
            ],
            tools=specs,
            tool_choice="required",
            temperature=0.2,
            seed=42,
            metadata={
                "role": RoleName.CODER.value,
                "loop_index": str(ctx.loop_index),
                "task_key": ctx.task_id,
                "context_digest": "act",
            },
        )
        result = await ctx.provider.chat(request)
        return [
            {
                "id": call.id,
                "name": call.name,
                "arguments": dict(call.arguments or {}),
            }
            for call in result.tool_calls
        ]
