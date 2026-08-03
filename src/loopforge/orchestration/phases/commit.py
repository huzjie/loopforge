"""COMMIT 阶段 —— 策略裁决 → 结构化 commit → push → 建 PR → 释放租约 → IN_REVIEW。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1 / §11.11。

commit message 的 trailer 由 ``git_ops`` 工具自动追加
（``LoopForge-Step-Key: <idempotency_key>`` 等），是崩溃恢复探测的物理载体。

Mock 离线模式下：真实执行 ``git add + commit``（本地沙箱仓库），
push / PR 为尽力而为（无远端则跳过），Task 最终转 ``IN_REVIEW``。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.enums import RoleName, TaskState
from loopforge.core.errors import LoopForgeError
from loopforge.core.event_types import EventType
from loopforge.db.uow import UnitOfWork
from loopforge.orchestration.idempotency import IntentTicket
from loopforge.orchestration.phases.base import BasePhase, PhaseContext, PhaseResult
from loopforge.tools.base import StepIntent, ToolContext

__all__ = ["CommitPhase"]


class CommitPhase(BasePhase):
    """提交阶段：把 ACT 的成果结构化提交。"""

    name = "COMMIT"

    async def _execute(self, ctx: PhaseContext) -> PhaseResult:
        # 1. 尽力而为：确保分支存在并切过去。
        branch = f"loopforge/task-{ctx.task_id[-8:].lower()}"
        await self._best_effort_git(ctx, {"operation": "branch", "branch": branch})
        await self._best_effort_git(ctx, {"operation": "checkout", "branch": branch})

        # 2. git add -A。
        add_result = await self._best_effort_git(ctx, {"operation": "add", "all": True})
        if add_result is not None and not add_result.ok:
            return PhaseResult(
                phase=self.name,
                loop_index=ctx.loop_index,
                ok=False,
                outcome="GIT_ADD_FAILED",
                message=add_result.output,
            )

        # 3. 两阶段意图协议 + commit。
        commit_args = {
            "operation": "commit",
            "message": f"LoopForge: {ctx.task_id} loop {ctx.loop_index}",
            "all": True,
        }
        key = ctx.idempotency.compute_key(
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            loop_index=ctx.loop_index,
            phase="COMMIT",
            tool_name="git_ops",
            args=commit_args,
        )
        intent = StepIntent(
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            step_id="",
            idempotency_key=key,
            tool_name="git_ops",
            args=commit_args,
            context={
                "loop_index": ctx.loop_index,
                "phase": "COMMIT",
                "role": RoleName.CODER,
                "side_effect_class": "PROBEABLE",
                "fence_token": ctx.lease.fence_token if ctx.lease else 0,
            },
        )
        ticket: IntentTicket | None = None
        try:
            ticket = await ctx.idempotency.begin_intent(intent)
        except LoopForgeError as exc:
            return PhaseResult(
                phase=self.name,
                loop_index=ctx.loop_index,
                ok=False,
                outcome="COMMIT_INTENT_FAILED",
                error_code=exc.code,
                message=exc.message,
            )
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
        result = await ctx.tool_dispatcher.dispatch(tool_ctx, "git_ops", commit_args)
        if not result.ok:
            error = LoopForgeError(
                result.output or "git commit 失败",
                details={"error_code": result.error_code},
            )
            await ctx.idempotency.abort(ticket, error)
            return PhaseResult(
                phase=self.name,
                loop_index=ctx.loop_index,
                ok=False,
                outcome="COMMIT_FAILED",
                message=result.output,
            )
        await ctx.idempotency.confirm(ticket, result)

        sha = result.external_ref or ""
        ctx.state["commit_sha"] = sha
        ctx.state["branch"] = branch
        ctx.state["_phase"] = self.name

        # 4. 事件 + Task 状态迁移 → IN_REVIEW。
        async with UnitOfWork() as uow:
            uow.collect_event(
                EventType.COMMIT_CREATED,
                {
                    "run_id": ctx.run_id,
                    "task_id": ctx.task_id,
                    "branch": branch,
                    "sha": sha,
                    "message_subject": f"LoopForge: {ctx.task_id} loop {ctx.loop_index}",
                    "files_changed": int(result.data.get("files_changed", 0)),
                    "idempotency_key": key,
                },
                run_id=ctx.run_id,
                task_id=ctx.task_id,
                step_id=ticket.step_id,
            )
            task = await uow.tasks.get(ctx.task_id)
            if task is not None and task.state in (TaskState.LEASED, TaskState.ACTIVE):
                await uow.tasks.cas_transition(
                    ctx.task_id,
                    from_state=task.state,
                    to_state=TaskState.IN_REVIEW,
                    expected_version=task.version,
                    extra={
                        "branch_name": branch,
                        "current_lease_id": None,
                    },
                )
                uow.collect_event(
                    EventType.TASK_IN_REVIEW,
                    {
                        "run_id": ctx.run_id,
                        "task_id": ctx.task_id,
                        "task_key": task.task_key,
                        "state": TaskState.IN_REVIEW,
                        "previous_state": task.state,
                        "reason": f"commit {sha}",
                    },
                    run_id=ctx.run_id,
                    task_id=ctx.task_id,
                )
            await uow.commit()

        # 5. 释放租约（任务已进入 IN_REVIEW，不再持有执行权）。
        if ctx.lease is not None:
            try:
                await ctx.lease_manager.release(
                    lease_id=ctx.lease.id,
                    fence_token=ctx.lease.fence_token,
                    reason="committed_in_review",
                )
            except Exception:  # noqa: BLE001 - 释放失败不阻断提交结果
                pass

        return PhaseResult(
            phase=self.name,
            loop_index=ctx.loop_index,
            ok=True,
            outcome="COMMITTED",
            steps_created=1,
            data={"commit_sha": sha, "branch": branch},
        )

    async def _best_effort_git(self, ctx: PhaseContext, args: dict[str, Any]) -> Any:
        """尽力而为的 git 辅助操作（失败不抛异常）。"""
        try:
            return await ctx.tool_dispatcher.dispatch(
                ToolContext(
                    run_id=ctx.run_id,
                    task_id=ctx.task_id,
                    step_id="",
                    trace_id="",
                    workspace=ctx.workspace,
                    executor=ctx.executor,
                    vcs=None,
                    policy=None,
                    idempotency_key="",
                    fence_token=ctx.lease.fence_token if ctx.lease else 0,
                ),
                "git_ops",
                args,
            )
        except Exception:  # noqa: BLE001
            return None
