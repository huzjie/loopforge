"""Shell / Git 工具测试 —— run_shell 与 git_ops 的 commit/probe 链路。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from loopforge.core.enums import PolicyDecision, SideEffectClass
from loopforge.executors.base import CommandSpec, WorkspaceSpec
from loopforge.executors.local import LocalSubprocessExecutor
from loopforge.executors.policy_guard import (
    CommandPolicyGuard,
    PolicyAction,
    PolicyVerdict,
)
from loopforge.tools import StepIntent, ToolContext
from loopforge.tools.fs_write import WriteFileTool
from loopforge.tools.git_ops import GitOpsTool
from loopforge.tools.shell import RunShellTool

PY = sys.executable


class DenyPythonGuard(CommandPolicyGuard):
    """测试用策略：拒绝所有 python 命令。"""

    async def evaluate_action(self, action: PolicyAction) -> PolicyVerdict:
        if action.argv:
            head = action.argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
            if head in ("python", "python.exe", "py"):
                return PolicyVerdict(
                    decision=PolicyDecision.DENY,
                    rule_id="deny.test_python",
                    reason="测试策略拒绝 python",
                )
        return await super().evaluate_action(action)


@pytest.fixture()
async def env(tmp_path: Path) -> tuple[LocalSubprocessExecutor, object, ToolContext]:
    """执行器 + 工作区 + 上下文。"""
    executor = LocalSubprocessExecutor(
        workspace_root=tmp_path / "ws",
        policy=CommandPolicyGuard(),
    )
    workspace = await executor.prepare(
        WorkspaceSpec(run_id="run-shell", task_id="task-shell", kind="task")
    )
    ctx = ToolContext(
        run_id="run-shell",
        task_id="task-shell",
        step_id="step-1",
        trace_id="trace-1",
        workspace=workspace,
        executor=executor,
        idempotency_key="idem_shell_abc123",
        fence_token=7,
    )
    return executor, workspace, ctx


async def test_run_shell_argv(env: object) -> None:
    """run_shell 用 argv 数组执行。"""
    _, _, ctx = env
    tool = RunShellTool()
    result = await tool.execute(ctx, tool.args_schema(argv=[PY, "-c", "print('shell ok')"]))
    assert result.ok
    assert "shell ok" in result.output
    assert result.data["exit_code"] == 0


async def test_run_shell_command_string(env: object) -> None:
    """run_shell 用 command 字符串（shlex 拆分，不经 shell）。"""
    _, _, ctx = env
    tool = RunShellTool()
    # 用正斜杠路径避免 shlex 把反斜杠当转义符。
    py_posix = PY.replace("\\", "/")
    result = await tool.execute(
        ctx, tool.args_schema(command=f"{py_posix} -c \"print('str ok')\"")
    )
    assert result.ok, result.output
    assert "str ok" in result.output


async def test_run_shell_policy_denied(env: object) -> None:
    """run_shell 危险命令被策略拒绝。"""
    _, _, ctx = env
    tool = RunShellTool()
    result = await tool.execute(ctx, tool.args_schema(command="rm -rf /"))
    assert not result.ok
    assert result.error_code == "LF-POLICY-1001" or "deny" in (result.error_code or "")


async def test_run_shell_side_effect_unsafe(env: object) -> None:
    """run_shell 副作用分级 = UNSAFE。"""
    assert RunShellTool.side_effect_class is SideEffectClass.UNSAFE
    _, _, ctx = env
    tool = RunShellTool()
    intent = StepIntent(
        run_id="run-shell",
        task_id="task-shell",
        step_id="step-1",
        idempotency_key="k",
        tool_name="run_shell",
        args={},
    )
    probe = await tool.probe(ctx, intent)
    assert not probe.executed
    assert probe.confidence == "unknown"


async def test_run_shell_policy_action_hook(env: object) -> None:
    """注入 policy 时先过 evaluate_action。"""
    _, _, ctx = env
    ctx.policy = DenyPythonGuard()
    tool = RunShellTool()
    result = await tool.execute(ctx, tool.args_schema(argv=[PY, "-c", "print(1)"]))
    assert not result.ok
    assert result.error_code == "deny.test_python"


# ---------------------------------------------------------------------------
# git_ops
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git 不可用")
async def test_git_ops_full_cycle(env: object) -> None:
    """git_ops：init → add → commit → log → probe。"""
    _, _, ctx = env
    executor: LocalSubprocessExecutor = ctx.executor

    # 手动 git init + 配置（git_ops 不支持 init 操作）。
    init = await executor.run(
        CommandSpec(handle_id=ctx.workspace.id, argv=["git", "init", "-b", "main"])
    )
    assert init.ok, init.stderr
    await executor.run(
        CommandSpec(
            handle_id=ctx.workspace.id, argv=["git", "config", "user.email", "agent@lf.local"]
        )
    )
    await executor.run(
        CommandSpec(
            handle_id=ctx.workspace.id, argv=["git", "config", "user.name", "Agent"]
        )
    )

    writer = WriteFileTool()
    await writer.execute(ctx, writer.args_schema(path="app.txt", content="v1"))

    tool = GitOpsTool()
    status = await tool.execute(ctx, tool.args_schema(operation="status"))
    assert status.ok

    add = await tool.execute(ctx, tool.args_schema(operation="add", all=True))
    assert add.ok, add.output

    commit = await tool.execute(
        ctx, tool.args_schema(operation="commit", message="feat: init app")
    )
    assert commit.ok, commit.output
    assert commit.external_ref  # commit sha

    log = await tool.execute(ctx, tool.args_schema(operation="log"))
    assert log.ok
    assert "feat: init app" in log.output
    # trailer 必须出现在 commit message 完整正文里（--oneline 只显示 subject）。
    body = await executor.run(
        CommandSpec(
            handle_id=ctx.workspace.id,
            argv=["git", "log", "-1", "--format=%B"],
        )
    )
    assert body.ok
    assert "LoopForge-Idempotency-Key: idem_shell_abc123" in body.stdout
    assert "LoopForge-Run-Id: run-shell" in body.stdout
    assert "LoopForge-Fence-Token: 7" in body.stdout

    # probe：按幂等键确认 commit 已存在。
    intent = StepIntent(
        run_id="run-shell",
        task_id="task-shell",
        step_id="step-1",
        idempotency_key="idem_shell_abc123",
        tool_name="git_ops",
        args={"operation": "commit", "repo_path": "."},
    )
    probe = await tool.probe(ctx, intent)
    assert probe.executed
    assert probe.confidence == "high"
    # commit 工具返回短 sha（7 位），probe 的 git log 返回完整 sha。
    assert probe.external_ref and commit.external_ref
    assert probe.external_ref.startswith(commit.external_ref)


@pytest.mark.skipif(shutil.which("git") is None, reason="git 不可用")
async def test_git_ops_probe_not_executed(env: object) -> None:
    """未 commit 时 probe 返回 executed=False。"""
    _, _, ctx = env
    executor: LocalSubprocessExecutor = ctx.executor
    init = await executor.run(
        CommandSpec(handle_id=ctx.workspace.id, argv=["git", "init", "-b", "main"])
    )
    assert init.ok
    tool = GitOpsTool()
    intent = StepIntent(
        run_id="run-shell",
        task_id="task-shell",
        step_id="step-1",
        idempotency_key="idem_never_committed",
        tool_name="git_ops",
        args={"operation": "commit", "repo_path": "."},
    )
    probe = await tool.probe(ctx, intent)
    assert not probe.executed


async def test_git_ops_branch(env: object) -> None:
    """git_ops branch 创建与探测。"""
    if shutil.which("git") is None:
        pytest.skip("git 不可用")
    _, _, ctx = env
    executor: LocalSubprocessExecutor = ctx.executor
    init = await executor.run(
        CommandSpec(handle_id=ctx.workspace.id, argv=["git", "init", "-b", "main"])
    )
    assert init.ok
    await executor.run(
        CommandSpec(
            handle_id=ctx.workspace.id, argv=["git", "config", "user.email", "agent@lf.local"]
        )
    )
    await executor.run(
        CommandSpec(
            handle_id=ctx.workspace.id, argv=["git", "config", "user.name", "Agent"]
        )
    )
    # 先落一个 commit，分支才能指向有效对象。
    writer = WriteFileTool()
    await writer.execute(ctx, writer.args_schema(path="base.txt", content="base"))
    await executor.run(
        CommandSpec(handle_id=ctx.workspace.id, argv=["git", "add", "-A"])
    )
    base_commit = await executor.run(
        CommandSpec(
            handle_id=ctx.workspace.id,
            argv=["git", "commit", "-m", "base"],
        )
    )
    assert base_commit.ok, base_commit.stderr

    tool = GitOpsTool()
    branch = await tool.execute(
        ctx, tool.args_schema(operation="branch", branch="feature/foo")
    )
    assert branch.ok, branch.output
    intent = StepIntent(
        run_id="run-shell",
        task_id="task-shell",
        step_id="step-1",
        idempotency_key="k",
        tool_name="git_ops",
        args={"operation": "branch", "branch": "feature/foo", "repo_path": "."},
    )
    probe = await tool.probe(ctx, intent)
    assert probe.executed
    assert probe.external_ref == "feature/foo"
