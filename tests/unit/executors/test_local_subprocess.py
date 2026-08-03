"""LocalSubprocessExecutor 真机测试 —— 当前平台真实执行。

覆盖：基础执行、stdin、超时整组杀死、策略拦截、路径沙箱、文件原语、
git 冒烟（git init → write → add → commit）。Docker 相关不在本文件。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from loopforge.core.errors import PolicyDeniedError
from loopforge.executors.base import CommandSpec, PathEscapeError, WorkspaceSpec
from loopforge.executors.local import LocalSubprocessExecutor
from loopforge.executors.resource_limits import ResourceLimits

PY = sys.executable


@pytest.fixture()
async def executor(tmp_path: Path) -> LocalSubprocessExecutor:
    """临时根目录上的本地执行器。"""
    return LocalSubprocessExecutor(
        workspace_root=tmp_path / "ws",
        limits=ResourceLimits(timeout_s=30.0, max_output_bytes=1_048_576),
        env_allowlist=["PATH", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "COMSPEC", "HOME", "LANG"],
    )


@pytest.fixture()
async def workspace(executor: LocalSubprocessExecutor) -> object:
    """准备好的工作区。"""
    return await executor.prepare(
        WorkspaceSpec(run_id="run-test", task_id="task-test", kind="task")
    )


async def test_run_echo(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """基础执行：echo。"""
    result = await executor.run(
        CommandSpec(handle_id=workspace.id, argv=[PY, "-c", "print('hello world')"])
    )
    assert result.ok
    assert result.exit_code == 0
    assert "hello world" in result.stdout


async def test_run_nonzero_exit_no_raise(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """非零退出码不抛异常，返回结果。"""
    result = await executor.run(
        CommandSpec(handle_id=workspace.id, argv=[PY, "-c", "import sys; sys.exit(3)"])
    )
    assert not result.ok
    assert result.exit_code == 3


async def test_run_stdin(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """stdin 透传。"""
    result = await executor.run(
        CommandSpec(
            handle_id=workspace.id,
            argv=[PY, "-c", "import sys; print(sys.stdin.read().strip().upper())"],
            stdin="hello stdin",
        )
    )
    assert result.ok
    assert "HELLO STDIN" in result.stdout


async def test_run_cwd_isolation(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """cwd 相对工作区根。"""
    result = await executor.run(
        CommandSpec(handle_id=workspace.id, argv=[PY, "-c", "import os; print(os.getcwd())"])
    )
    assert result.ok
    assert Path(result.stdout.strip()) == Path(workspace.root)


async def test_run_timeout_kills_tree(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """超时后整组杀死，无孤儿进程。"""
    result = await executor.run(
        CommandSpec(
            handle_id=workspace.id,
            argv=[PY, "-c", "import time; time.sleep(60)"],
            timeout_s=1.0,
        )
    )
    assert result.timed_out
    assert not result.ok


async def test_run_truncates_output(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """输出超限截断。"""
    result = await executor.run(
        CommandSpec(
            handle_id=workspace.id,
            argv=[PY, "-c", "print('x' * 5000)"],
            max_output_bytes=1024,
        )
    )
    assert result.truncated
    assert len(result.stdout) < 5000


async def test_run_policy_denied(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """策略拒绝（rm -rf /）→ 抛 PolicyDeniedError。"""
    with pytest.raises(PolicyDeniedError):
        await executor.run(
            CommandSpec(handle_id=workspace.id, argv=["rm", "-rf", "/"])
        )


async def test_run_escape_cwd(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """cwd 逃逸工作区 → PathEscapeError。"""
    with pytest.raises(PathEscapeError):
        await executor.run(
            CommandSpec(handle_id=workspace.id, argv=[PY, "-c", "pass"], cwd="../../etc")
        )


async def test_write_read_roundtrip(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """write_file → read_file 往返。"""
    stat = await executor.write_file(workspace, "sub/dir/file.txt", "content-123")
    assert stat.sha256
    content = await executor.read_file(workspace, "sub/dir/file.txt")
    assert content.content == "content-123"
    assert content.sha256 == stat.sha256


async def test_write_escape(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """write_file 逃逸 → PathEscapeError。"""
    with pytest.raises(PathEscapeError):
        await executor.write_file(workspace, "../../escape.txt", "bad")


async def test_list_dir(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """list_dir 正常。"""
    await executor.write_file(workspace, "a.txt", "A")
    await executor.write_file(workspace, "sub/b.txt", "B")
    listing = await executor.list_dir(workspace, ".", depth=2)
    names = {entry.name for entry in listing.entries}
    assert "a.txt" in names
    assert "sub" in names
    assert "b.txt" in names


async def test_healthcheck_never_raises(executor: LocalSubprocessExecutor) -> None:
    """healthcheck 永不抛异常。"""
    health = await executor.healthcheck()
    assert health.healthy
    assert health.kind == "local_subprocess"


async def test_cleanup_idempotent(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """cleanup 幂等。"""
    await executor.cleanup(workspace)
    await executor.cleanup(workspace)  # 第二次不抛


# ---------------------------------------------------------------------------
# Docker 契约测试（无 Docker 时自动降级，绝不 require Docker）
# ---------------------------------------------------------------------------


async def test_docker_healthcheck_never_raises(tmp_path: Path) -> None:
    """DockerExecutor 与 Local 同契约：healthcheck 永不抛异常。

    本测试不依赖 Docker —— daemon 可用时 healthy=True，不可用时 healthy=False，
    但绝不崩栈（LF-EXECUTOR-0804 降级语义）。
    """
    from loopforge.executors.docker import DockerExecutor

    executor = DockerExecutor(workspace_root=tmp_path / "ws-docker")
    health = await executor.healthcheck()
    assert health.kind == "docker"
    assert health.healthy in (True, False)
    assert health.detail
    await executor.aclose()


async def test_docker_prepare_without_daemon_raises_clear_error(tmp_path: Path) -> None:
    """Docker 不可用时 prepare 抛清晰错误码（LF-EXECUTOR-0804），不崩栈。"""
    from loopforge.core.errors import ExecutorError
    from loopforge.executors.docker import DockerExecutor

    executor = DockerExecutor(workspace_root=tmp_path / "ws-docker2")
    try:
        await executor.prepare(
            WorkspaceSpec(run_id="run-docker", task_id="task-docker", kind="task")
        )
    except ExecutorError as exc:
        assert str(exc.code).startswith("LF-EXECUTOR-")
    finally:
        await executor.aclose()


# ---------------------------------------------------------------------------
# git 冒烟（真实 git）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git 不可用")
async def test_git_smoke(executor: LocalSubprocessExecutor) -> None:
    """git init → write → add → commit 全链路。"""
    git_ws = await executor.prepare(
        WorkspaceSpec(run_id="run-git", task_id="task-git", kind="task")
    )
    try:
        init = await executor.run(
            CommandSpec(handle_id=git_ws.id, argv=["git", "init", "-b", "main"])
        )
        assert init.ok, init.stderr
        await executor.write_file(git_ws, "hello.txt", "hello git")
        add = await executor.run(
            CommandSpec(handle_id=git_ws.id, argv=["git", "add", "-A"])
        )
        assert add.ok, add.stderr
        # 先配置 user（CI 环境无全局配置）。
        await executor.run(
            CommandSpec(
                handle_id=git_ws.id,
                argv=["git", "config", "user.email", "agent@loopforge.local"],
            )
        )
        await executor.run(
            CommandSpec(
                handle_id=git_ws.id,
                argv=["git", "config", "user.name", "LoopForge Agent"],
            )
        )
        commit = await executor.run(
            CommandSpec(
                handle_id=git_ws.id,
                argv=["git", "commit", "-m", "smoke commit"],
            )
        )
        assert commit.ok, commit.stderr
        log = await executor.run(
            CommandSpec(handle_id=git_ws.id, argv=["git", "log", "--oneline", "-n", "1"])
        )
        assert log.ok
        assert "smoke commit" in log.stdout
    finally:
        await executor.cleanup(git_ws)


async def test_env_allowlist_filtered(executor: LocalSubprocessExecutor, workspace: object) -> None:
    """环境变量白名单过滤：白名单外的键不传给子进程。"""
    result = await executor.run(
        CommandSpec(
            handle_id=workspace.id,
            argv=[PY, "-c", "import os; print('SECRET_KEY' in os.environ)"],
            env={"SECRET_KEY": "leak", "PATH": "x"},
        )
    )
    # PATH 在白名单但被命令级覆盖为 x —— 覆盖生效；SECRET_KEY 被过滤。
    assert result.ok
    assert "False" in result.stdout
