"""``git_ops`` 工具 —— Git 操作（``PROBEABLE``，靠 trailer 探测）。

支持操作
--------
``status`` / ``diff`` / ``add`` / ``commit`` / ``branch`` / ``checkout`` /
``log`` / ``reset``。全部通过执行器 ``run`` 调用**真 git**，不自己解析对象库。

幂等契约（§8.1.5 强制约定）
---------------------------
* ``commit`` 的 message 必须包含 trailer 块：``LoopForge-Run-Id`` /
  ``LoopForge-Task-Id`` / ``LoopForge-Step-Id`` / ``LoopForge-Idempotency-Key`` /
  ``LoopForge-Fence-Token``（键名来自 ``loopforge.constants`` 冻结常量）。
* :meth:`probe` 对 ``commit`` 用 ``git log --all --grep=<idempotency_key>``
  探测 —— 幂等键是每步唯一哈希，比按 trailer 名 grep 更稳。
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from loopforge.constants import (
    TRAILER_FENCE_TOKEN,
    TRAILER_IDEMPOTENCY_KEY,
    TRAILER_RUN_ID,
    TRAILER_STEP_ID,
    TRAILER_TASK_ID,
)
from loopforge.core.enums import SideEffectClass
from loopforge.tools.base import BaseTool, ProbeResult, StepIntent, ToolContext, ToolResult

__all__ = ["GitOpsArgs", "GitOpsTool", "build_commit_trailers", "git_probe_commit"]


class GitOpsArgs(BaseModel):
    """Git 操作参数。"""

    operation: str = Field(
        pattern="^(status|diff|add|commit|branch|checkout|log|reset)$",
        description="status / diff / add / commit / branch / checkout / log / reset",
    )
    repo_path: str = Field(default=".", description="仓库目录（相对工作区根）")
    message: str | None = Field(default=None, description="commit message")
    branch: str | None = Field(default=None, description="branch 操作的名称")
    files: list[str] = Field(default_factory=list, description="add / checkout 的文件列表")
    all: bool = Field(default=False, description="add 全部改动")
    reset_hard: bool = Field(default=False, description="reset --hard（需审批规则放行）")
    timeout_s: float = Field(default=120.0, gt=0, le=3600)


def build_commit_trailers(ctx: ToolContext) -> str:
    """构造 commit trailer 块（幂等探测凭据）。

    Args:
        ctx: 工具上下文。

    Returns:
        追加在 commit message 末尾的 trailer 文本。
    """
    return (
        f"\n\n{TRAILER_RUN_ID}: {ctx.run_id}\n"
        f"{TRAILER_TASK_ID}: {ctx.task_id}\n"
        f"{TRAILER_STEP_ID}: {ctx.step_id}\n"
        f"{TRAILER_IDEMPOTENCY_KEY}: {ctx.idempotency_key}\n"
        f"{TRAILER_FENCE_TOKEN}: {ctx.fence_token}"
    )


def _make_spec(executor: object, handle_id: str, argv: list[str], cwd: str, timeout_s: float) -> object:
    """构造 CommandSpec。"""
    from loopforge.executors.base import CommandSpec

    return CommandSpec(handle_id=handle_id, argv=argv, cwd=cwd, timeout_s=timeout_s)


class GitOpsTool(BaseTool):
    """Git 操作工具。"""

    name: ClassVar[str] = "git_ops"
    description: ClassVar[str] = (
        "在工作区仓库执行 Git 操作：status / diff / add / commit / branch / "
        "checkout / log / reset。commit 自动附带 LoopForge 幂等 trailer，"
        "崩溃后可探测确认。"
    )
    args_schema: ClassVar[type[BaseModel]] = GitOpsArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.PROBEABLE

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """执行 Git 操作。"""
        parsed = args.model_dump()
        if ctx.executor is None or ctx.workspace is None:
            return ToolResult.failure("executor / workspace 未注入", error_code="LF-TOOL-0902")

        operation = parsed["operation"]
        argv = self._build_argv(parsed, ctx)
        try:
            result = await ctx.executor.run(
                _make_spec(
                    ctx.executor,
                    ctx.workspace.id,
                    argv,
                    parsed["repo_path"],
                    parsed["timeout_s"],
                )
            )
        except Exception as exc:  # noqa: BLE001 - 策略拒绝等统一转 ToolResult
            return ToolResult.failure(
                f"git {operation} 失败：{exc}",
                error_code=getattr(exc, "code", "LF-TOOL-0902"),
            )

        output = result.combined_output(limit=4000)
        external_ref = None
        if operation == "commit" and result.ok:
            external_ref = _extract_commit_sha(result.stdout)
        return ToolResult(
            ok=result.ok,
            output=output,
            data={
                "operation": operation,
                "exit_code": result.exit_code,
                "external_ref": external_ref,
            },
            external_ref=external_ref,
            truncated=result.truncated,
            bytes_out=len(output.encode("utf-8")),
        )

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """按操作探测外部世界是否已产生副作用。"""
        operation = str(intent.args.get("operation", ""))
        if ctx.executor is None or ctx.workspace is None:
            return ProbeResult(executed=False, confidence="low", evidence="executor 未注入")
        repo_path = str(intent.args.get("repo_path", "."))
        key = intent.idempotency_key or str(intent.args.get("key", ""))

        if operation == "commit":
            if not key:
                return ProbeResult(executed=False, confidence="low", evidence="缺少 idempotency_key")
            return await git_probe_commit(ctx.executor, ctx.workspace, repo_path, key)
        if operation == "branch":
            branch = str(intent.args.get("branch", ""))
            if not branch:
                return ProbeResult(executed=False, confidence="low", evidence="缺少 branch")
            try:
                result = await ctx.executor.run(
                    _make_spec(
                        ctx.executor,
                        ctx.workspace.id,
                        ["git", "rev-parse", "--verify", "--quiet", branch],
                        repo_path,
                        30.0,
                    )
                )
            except Exception:  # noqa: BLE001
                return ProbeResult(executed=False, confidence="medium", evidence="rev-parse 异常")
            executed = result.ok
            return ProbeResult(
                executed=executed,
                external_ref=branch if executed else None,
                confidence="high",
                evidence=f"git rev-parse --verify {branch} exit={result.exit_code}",
            )
        if operation in ("add", "checkout", "reset", "status", "diff", "log"):
            return ProbeResult(
                executed=False,
                confidence="unknown",
                evidence=f"git_ops:{operation} 视为可重放（PURE 语义）或需人工确认",
            )
        return ProbeResult(executed=False, confidence="unknown", evidence=f"未知操作 {operation}")

    # ------------------------------------------------------------------ 内部

    def _build_argv(self, parsed: dict, ctx: ToolContext) -> list[str]:
        """按操作构造 git argv。"""
        operation = parsed["operation"]
        files = parsed.get("files") or []
        if operation == "status":
            return ["git", "status", "--short", "--branch"]
        if operation == "diff":
            return ["git", "diff", "--stat", *files]
        if operation == "add":
            if parsed.get("all"):
                return ["git", "add", "-A"]
            if files:
                return ["git", "add", *files]
            return ["git", "add", "-A"]
        if operation == "commit":
            message = parsed.get("message") or "LoopForge automated commit"
            message = message + build_commit_trailers(ctx)
            return ["git", "commit", "-m", message]
        if operation == "branch":
            branch = parsed.get("branch") or ""
            if not branch:
                return ["git", "branch", "--list"]
            return ["git", "branch", branch]
        if operation == "checkout":
            branch = parsed.get("branch") or ""
            if files:
                return ["git", "checkout", *files]
            if branch:
                return ["git", "checkout", branch]
            return ["git", "checkout"]
        if operation == "log":
            return ["git", "log", "--oneline", "-n", "20"]
        if operation == "reset":
            if parsed.get("reset_hard"):
                return ["git", "reset", "--hard"]
            return ["git", "reset"]
        return ["git", "status"]


def _extract_commit_sha(stdout: str) -> str | None:
    """从 commit 输出提取短 SHA（``[main abc1234] message``）。"""
    import re

    match = re.search(r"\[[^\]]+\s+([0-9a-f]{7,40})\]", stdout)
    return match.group(1) if match else None


async def git_probe_commit(
    executor: object,
    workspace: object,
    repo_path: str,
    idempotency_key: str,
) -> ProbeResult:
    """探测 commit 是否已存在（按幂等键 grep）。

    Args:
        executor: 执行器。
        workspace: 工作区句柄。
        repo_path: 仓库目录。
        idempotency_key: 幂等键。

    Returns:
        :class:`ProbeResult`。
    """
    try:
        result = await executor.run(
            _make_spec(
                executor,
                workspace.id,
                ["git", "log", "--all", f"--grep={idempotency_key}", "--format=%H", "-1"],
                repo_path,
                30.0,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 探测异常按未执行处理
        return ProbeResult(executed=False, confidence="low", evidence=f"git log 异常：{exc}")
    sha = result.stdout.strip()
    if result.ok and sha:
        return ProbeResult(
            executed=True,
            external_ref=sha,
            confidence="high",
            evidence=f"git log 命中幂等键 {idempotency_key} → {sha}",
        )
    return ProbeResult(
        executed=False,
        confidence="medium",
        evidence=f"git log 未命中幂等键 {idempotency_key}（exit={result.exit_code}）",
    )
