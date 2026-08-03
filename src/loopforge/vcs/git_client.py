"""``GitClient`` —— 本地 Git 客户端（subprocess 封装）。

设计（§8.1.2）
--------------
* **优先经 Executor 走策略**：调用方传入已 ``prepare()`` 的
  :class:`~loopforge.executors.base.BaseExecutor` 与
  :class:`~loopforge.executors.base.WorkspaceHandle` 时，所有命令经
  ``executor.run(CommandSpec(...))`` 执行（命令策略 / 路径沙箱 / 资源限额
  四道防线全开）。
* **降级直连**：未注入 Executor（如 local_sandbox 的 bare 仓库操作、纯脚本
  场景）时，用 ``asyncio.create_subprocess_exec`` 直连 —— **永远不用
  ``shell=True``**，argv 数组语义跨平台一致。
* **全部真调 git**：clone / fetch / checkout / add / commit / push / log /
  diff / status / rev-parse / merge / branch，不做对象库解析。
* Windows 兼容：路径统一 ``pathlib.Path``；git 经 ``shutil.which`` 解析。

约定
----
* :meth:`commit` 支持传入结构化 message（见 :mod:`loopforge.vcs.commit_message`），
  自动附带四个冻结 trailer，供 ``git_ops.probe()`` 幂等探测。
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from loopforge.core.logging import get_logger
from loopforge.core.errors import VcsError
from loopforge.executors.base import BaseExecutor, CommandSpec, WorkspaceHandle

__all__ = ["GitClient", "GitResult", "GitCommandError"]

_LOG = get_logger(__name__)


class GitCommandError(VcsError):
    """git 命令失败。"""


class GitResult:
    """一次 git 命令的结果。"""

    __slots__ = ("ok", "exit_code", "stdout", "stderr", "argv")

    def __init__(self, ok: bool, exit_code: int, stdout: str, stderr: str, argv: list[str]) -> None:
        self.ok = ok
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.argv = list(argv)

    @property
    def combined(self) -> str:
        """stdout + stderr 合并（调试 / 日志用）。"""
        return f"{self.stdout}\n{self.stderr}".strip()

    def require_ok(self, what: str) -> "GitResult":
        """失败时抛 :class:`GitCommandError`。"""
        if not self.ok:
            raise GitCommandError(
                f"git {what} 失败（exit={self.exit_code}）：{self.stderr[:2000]}",
                details={
                    "argv": self.argv,
                    "exit_code": self.exit_code,
                    "stderr": self.stderr[:2000],
                },
            )
        return self


class GitClient:
    """本地 Git 客户端。

    Args:
        cwd: 仓库目录（默认当前目录）。
        executor: 可选的执行器（注入后所有命令经策略执行）。
        workspace: 可选的执行器工作区句柄（与 ``executor`` 配套）。
        git_bin: git 可执行文件路径；``None`` 自动探测。
        timeout_s: 单命令超时。
    """

    def __init__(
        self,
        cwd: str | Path = ".",
        *,
        executor: BaseExecutor | None = None,
        workspace: WorkspaceHandle | None = None,
        git_bin: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.cwd: Path = Path(cwd).expanduser().resolve()
        self.executor: BaseExecutor | None = executor
        self.workspace: WorkspaceHandle | None = workspace
        self.git_bin: str = git_bin or shutil.which("git") or "git"
        self.timeout_s: float = timeout_s

    # ------------------------------------------------------------------ 基础

    async def run(self, args: Sequence[str], *, cwd: str | Path | None = None) -> GitResult:
        """执行一条 git 命令。

        Args:
            args: git 子命令参数（不含 ``git`` 本身）。
            cwd: 覆盖工作目录。

        Returns:
            :class:`GitResult`。

        Raises:
            GitCommandError: 执行器策略拒绝或命令异常。
        """
        argv = [self.git_bin, *list(args)]
        target = str(Path(cwd) if cwd is not None else self.cwd)
        if self.executor is not None and self.workspace is not None:
            spec = CommandSpec(
                handle_id=self.workspace.id,
                argv=argv,
                cwd=target,
                timeout_s=self.timeout_s,
            )
            try:
                result = await self.executor.run(spec)
            except Exception as exc:  # noqa: BLE001 - 策略拒绝等统一转 VcsError
                raise GitCommandError(
                    f"git 命令被执行层拒绝：{exc}",
                    details={"argv": argv, "cwd": target},
                ) from exc
            return GitResult(
                ok=result.ok,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                argv=argv,
            )
        # 降级直连（无 Executor）
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_s
            )
        except asyncio.TimeoutError as exc:
            raise GitCommandError(
                f"git 命令超时（>{self.timeout_s}s）：{argv}",
                details={"argv": argv, "cwd": target},
            ) from exc
        except OSError as exc:
            raise GitCommandError(
                f"git 无法启动：{exc}", details={"argv": argv, "cwd": target}
            ) from exc
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        return GitResult(
            ok=proc.returncode == 0,
            exit_code=proc.returncode or 0,
            stdout=stdout,
            stderr=stderr,
            argv=argv,
        )

    # ------------------------------------------------------------------ 常用操作

    async def status(self, *, short: bool = True) -> GitResult:
        """工作区状态。"""
        return await self.run(["status", "--short", "--branch"] if short else ["status"])

    async def diff(self, *paths: str) -> GitResult:
        """未暂存 diff（纯文本）。"""
        return await self.run(["diff", *paths])

    async def diff_staged(self, *paths: str) -> GitResult:
        """已暂存 diff。"""
        return await self.run(["diff", "--cached", *paths])

    async def add(self, paths: Sequence[str] | None = None, *, all: bool = False) -> GitResult:
        """暂存文件。"""
        if all or not paths:
            return await self.run(["add", "-A"])
        return await self.run(["add", *list(paths)])

    async def commit(self, message: str, *, paths: Sequence[str] | None = None) -> GitResult:
        """提交（带冻结 trailer 的 message 由调用方构造）。"""
        if paths:
            await self.add(paths)
        else:
            await self.add(all=True)
        return await self.run(["commit", "-m", message])

    async def branch(self, name: str, *, base: str | None = None) -> GitResult:
        """创建分支。"""
        argv = ["branch", name]
        if base:
            argv += [base]
        return await self.run(argv)

    async def checkout(self, ref: str, *, create: bool = False) -> GitResult:
        """切换分支 / 检出。"""
        argv = ["checkout"]
        if create:
            argv += ["-b"]
        argv += [ref]
        return await self.run(argv)

    async def checkout_files(self, *paths: str) -> GitResult:
        """丢弃指定文件的未提交改动。"""
        return await self.run(["checkout", "--", *paths])

    async def log(
        self,
        *,
        count: int = 20,
        format: str | None = None,
        all: bool = False,
        trailers: bool = False,
    ) -> GitResult:
        """提交日志。

        Args:
            count: 条数。
            format: ``--format`` 模板；``None`` 用 oneline。
            all: 是否包含所有分支（``--all``）。
            trailers: 是否追加 ``%(trailers)``（幂等探测用）。
        """
        argv = ["log"]
        if all:
            argv += ["--all"]
        if trailers:
            argv += ["--format=%(H)%x00%(trailers)", "-n", str(count)]
        elif format:
            argv += [f"--format={format}", "-n", str(count)]
        else:
            argv += ["--oneline", "-n", str(count)]
        return await self.run(argv)

    async def rev_parse(self, ref: str = "HEAD") -> GitResult:
        """解析引用为完整 sha。"""
        return await self.run(["rev-parse", ref])

    async def merge(self, branch: str, *, message: str = "") -> GitResult:
        """合并分支。"""
        argv = ["merge", branch]
        if message:
            argv += ["-m", message]
        return await self.run(argv)

    async def merge_abort(self) -> GitResult:
        """中止合并。"""
        return await self.run(["merge", "--abort"])

    async def rebase_abort(self) -> GitResult:
        """中止 rebase。"""
        return await self.run(["rebase", "--abort"])

    async def push(self, remote: str = "origin", branch: str = "", *, force: bool = False) -> GitResult:
        """推送分支。"""
        argv = ["push", remote]
        if force:
            argv += ["--force"]
        if branch:
            argv += [branch]
        return await self.run(argv)

    async def pull(self, remote: str = "origin", branch: str = "") -> GitResult:
        """拉取。"""
        argv = ["pull", remote]
        if branch:
            argv += [branch]
        return await self.run(argv)

    async def fetch(self, remote: str = "origin") -> GitResult:
        """抓取远端。"""
        return await self.run(["fetch", remote])

    async def init(self, *, bare: bool = False) -> GitResult:
        """初始化仓库。"""
        argv = ["init"]
        if bare:
            argv += ["--bare"]
        return await self.run(argv)

    async def clone(self, remote: str, dest: str | Path, *, branch: str | None = None) -> GitResult:
        """克隆仓库。"""
        argv = ["clone", remote, str(dest)]
        if branch:
            argv += ["--branch", branch]
        return await self.run(argv)

    async def config(self, key: str, value: str, *, local: bool = True) -> GitResult:
        """设置 git 配置（本地优先）。"""
        argv = ["config"]
        if local:
            argv += ["--local"]
        argv += [key, value]
        return await self.run(argv)

    async def current_branch(self) -> str:
        """当前分支名（``HEAD`` 分离时返回空串）。"""
        result = await self.run(["rev-parse", "--abbrev-ref", "HEAD"])
        if not result.ok:
            return ""
        name = result.stdout.strip()
        return "" if name == "HEAD" else name

    async def head_sha(self) -> str:
        """当前 HEAD 完整 sha。"""
        return (await self.rev_parse("HEAD")).stdout.strip()

    async def is_repo(self) -> bool:
        """目录是否已是一个 git 仓库。"""
        result = await self.run(["rev-parse", "--is-inside-work-tree"])
        return result.ok and result.stdout.strip() == "true"

    async def git_dir(self) -> Path | None:
        """仓库 .git 目录（非仓库返回 ``None``）。"""
        result = await self.run(["rev-parse", "--absolute-git-dir"])
        return Path(result.stdout.strip()) if result.ok else None

    async def ensure_identity(self, *, name: str = "LoopForge Bot", email: str = "bot@loopforge.local") -> None:
        """确保提交身份已配置（本地仓库级，避免全局污染）。"""
        check_name = await self.run(["config", "--local", "user.name"])
        if not check_name.ok or not check_name.stdout.strip():
            await self.config("user.name", name)
        check_email = await self.run(["config", "--local", "user.email"])
        if not check_email.ok or not check_email.stdout.strip():
            await self.config("user.email", email)
