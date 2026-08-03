"""``run_shell`` 工具 —— 执行命令（``UNSAFE``，不可探测）。

安全模型
--------
* **全仓禁止 ``shell=True``**：本工具接受 ``argv`` 数组；若传入 ``command``
  字符串则用 ``shlex.split`` 拆成 argv —— 但拆解后的 argv 只用于
  ``create_subprocess_exec``，不经过任何 shell 解释器，元字符（``;`` ``&&``
  ``|`` 等）对普通程序只是字面量。
* **双层策略**：工具层先过 ``ctx.policy``（若注入），执行器层再过一次
  :class:`~loopforge.executors.policy_guard.CommandPolicyGuard` —— 插件绕过了
  工具层也绕不过执行层这道物理出口。
* 恢复语义：``UNSAFE`` 不可探测，崩溃后一律 ``replan``（§8.1.4 决策表）。
"""

from __future__ import annotations

import shlex
from typing import ClassVar

from pydantic import BaseModel, Field

from loopforge.core.enums import PolicyDecision, SideEffectClass
from loopforge.tools.base import BaseTool, ProbeResult, StepIntent, ToolContext, ToolResult


class RunShellArgs(BaseModel):
    """执行命令参数。"""

    argv: list[str] = Field(
        default_factory=list, min_length=0, description="命令数组（推荐，禁止 shell 解释）"
    )
    command: str = Field(
        default="", max_length=8000, description="命令字符串（用 shlex 拆成 argv，不经 shell）"
    )
    cwd: str = Field(default=".", description="相对工作区根的工作目录")
    timeout_s: float = Field(default=120.0, gt=0, le=3600)
    network: str = Field(default="none", pattern="^(none|restricted)$")


class RunShellTool(BaseTool):
    """执行 shell 命令。"""

    name: ClassVar[str] = "run_shell"
    description: ClassVar[str] = (
        "在工作区内执行命令。优先传 argv 数组；或传 command 字符串（按 POSIX 规则拆成"
        "argv，不经 shell 解释，因此 ; && | 等元字符只是普通字符）。"
        "返回退出码、stdout、stderr 与耗时。"
    )
    args_schema: ClassVar[type[BaseModel]] = RunShellArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.UNSAFE

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """执行命令。"""
        parsed = args.model_dump()
        if ctx.executor is None or ctx.workspace is None:
            return ToolResult.failure("executor / workspace 未注入", error_code="LF-TOOL-0902")

        argv = _resolve_argv(parsed.get("argv") or [], parsed.get("command") or "")
        if not argv:
            return ToolResult.failure("argv 与 command 至少提供一个", error_code="LF-TOOL-0901")

        # 工具层策略（若注入）：非 ALLOW 直接拒绝。
        if ctx.policy is not None and hasattr(ctx.policy, "evaluate_action"):
            from loopforge.executors.policy_guard import PolicyAction

            action = PolicyAction(
                kind="command",
                tool_name=self.name,
                argv=argv,
                context={"run_id": ctx.run_id, "task_id": ctx.task_id, "step_id": ctx.step_id},
            )
            verdict = await ctx.policy.evaluate_action(action)
            if verdict.decision is not PolicyDecision.ALLOW:
                return ToolResult.failure(
                    f"命令被策略拒绝（{verdict.rule_id}）：{verdict.reason}",
                    error_code=verdict.rule_id or "LF-POLICY-1001",
                    decision=verdict.decision.value,
                )

        try:
            spec = _make_spec(ctx.executor, ctx.workspace.id, argv, parsed)
            result = await ctx.executor.run(spec)
        except Exception as exc:  # noqa: BLE001 - 策略拒绝 / 路径逃逸等统一转 ToolResult
            return ToolResult.failure(
                f"命令执行失败：{exc}",
                error_code=getattr(exc, "code", "LF-TOOL-0902"),
            )

        output = result.combined_output(limit=4000)
        return ToolResult(
            ok=result.ok,
            output=output,
            data={
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "timed_out": result.timed_out,
                "truncated": result.truncated,
                "argv": argv,
            },
            truncated=result.truncated,
            bytes_out=len(output.encode("utf-8")),
        )

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """UNSAFE：不可探测，恢复时一律 replan。"""
        _ = (ctx, intent)
        return ProbeResult(
            executed=False,
            confidence="unknown",
            evidence="run_shell 是 UNSAFE 工具，副作用不可探测，崩溃后一律 replan",
        )


def _resolve_argv(argv: list[str], command: str) -> list[str]:
    """把 argv / command 统一成 argv 数组。"""
    items = [str(item) for item in argv]
    if items:
        return items
    if command.strip():
        try:
            return shlex.split(command)
        except ValueError:
            # 引号不配对时退化为按空白切分，保证不抛异常。
            return command.split()
    return []


def _make_spec(executor: object, handle_id: str, argv: list[str], parsed: dict) -> object:
    """构造 CommandSpec（从 executors.base 导入，避免循环依赖）。"""
    from loopforge.executors.base import CommandSpec

    return CommandSpec(
        handle_id=handle_id,
        argv=argv,
        cwd=parsed["cwd"],
        timeout_s=parsed["timeout_s"],
        network=parsed["network"],
    )
