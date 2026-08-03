"""``LocalSubprocessExecutor`` —— 默认执行器（全平台可跑）。

为什么它是默认
--------------
自托管场景里 Docker 不一定可用；本地子进程执行是**唯一保证开箱即用的实现**。
本类在 ``ARCHITECTURE.md`` §7.2 冻结的 :class:`BaseExecutor` 契约之上，
补齐四道防线：

1. **路径沙箱**：所有 ``cwd`` / 文件路径先过
   :class:`~loopforge.executors.path_sandbox.PathSandbox`，拦截 ``..``、
   绝对路径、符号链接、Windows 盘符与 UNC。
2. **命令策略**：执行前过
   :class:`~loopforge.executors.policy_guard.CommandPolicyGuard`，
   白名单 + 黑名单 + 参数注入检测，命中即抛异常并写审计。
3. **资源限额**：超时 / 输出截断 / 进程组整组杀死（POSIX ``setsid``，
   Windows ``CREATE_NEW_PROCESS_GROUP`` + ``taskkill /T``）。
4. **环境变量白名单**：``cmd.env`` 与宿主 ``os.environ`` 都与白名单求交集，
   未列出的变量一律不传给子进程。

Windows 兼容要点
----------------
* 不用 ``shell=True``：``asyncio.create_subprocess_exec(argv, ...)`` 在 Windows
  上直接 ``CreateProcess``，argv 数组语义与 POSIX 一致。
* ``git`` / ``pytest`` 等经 ``shutil.which`` 解析，找不到时抛
  :class:`~loopforge.executors.base.InvalidCommandError`，错误信息可读。
* 超时后 ``taskkill /F /T /PID`` 递归杀进程树，避免孤儿进程占住文件句柄。
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from loopforge.core.clock import Clock, get_clock
from loopforge.core.config import ExecutorSettings, get_settings
from loopforge.core.enums import Severity
from loopforge.core.logging import get_logger
from loopforge.executors.base import (
    BaseExecutor,
    CommandResult,
    CommandSpec,
    DirListing,
    ExecutorHealth,
    FileContent,
    FileStat,
    InvalidCommandError,
    WorkspaceHandle,
    WorkspaceNotReadyError,
    WorkspaceSpec,
)
from loopforge.executors.path_sandbox import PathSandbox
from loopforge.executors.policy_guard import CommandPolicyGuard
from loopforge.executors.resource_limits import (
    ResourceLimits,
    kill_process_tree,
    spawn_kwargs,
)
from loopforge.executors.stream import StreamSink, pump_pair
from loopforge.executors.workspace import (
    WorkspaceManager,
    read_text_limited,
    stat_file,
    walk_listing,
    write_text_atomic,
)

__all__ = ["LocalSubprocessExecutor", "build_limits_from_settings"]

_log = get_logger(__name__)


def build_limits_from_settings(settings: ExecutorSettings | None = None) -> ResourceLimits:
    """从配置构造 :class:`ResourceLimits`。

    Args:
        settings: 执行器配置；``None`` 时取全局配置。

    Returns:
        :class:`ResourceLimits`。
    """
    cfg = settings or get_settings().executor
    return ResourceLimits(
        timeout_s=float(cfg.command_timeout_s),
        max_output_bytes=cfg.max_output_bytes,
        max_memory_mb=cfg.max_memory_mb,
    )


class LocalSubprocessExecutor(BaseExecutor):
    """本地子进程执行器。

    Args:
        workspace_root: 工作区父目录（默认取 ``settings.executor.workspace_root``）。
        policy: 命令策略守卫（默认新建）。
        clock: 时间源。
        limits: 资源限额。
        env_allowlist: 环境变量白名单（默认取配置）。
        allow_symlinks: 是否允许工作区内符号链接（默认禁止）。
        default_quota_bytes: 未在 spec 指定时的磁盘配额。
        cleanup_on_success: 成功后是否自动清理工作区（默认按配置）。
    """

    kind: ClassVar[str] = "local_subprocess"
    enabled: ClassVar[bool] = True

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        policy: CommandPolicyGuard | None = None,
        clock: Clock | None = None,
        limits: ResourceLimits | None = None,
        env_allowlist: list[str] | None = None,
        allow_symlinks: bool = False,
        default_quota_bytes: int | None = None,
        cleanup_on_success: bool | None = None,
    ) -> None:
        settings = get_settings().executor
        root = workspace_root if workspace_root is not None else settings.workspace_root
        self._clock: Clock = clock or get_clock()
        self._policy: CommandPolicyGuard = policy or CommandPolicyGuard()
        self._limits: ResourceLimits = limits or build_limits_from_settings(settings)
        self._env_allowlist: frozenset[str] = frozenset(
            env_allowlist if env_allowlist is not None else settings.env_allowlist
        )
        self._cleanup_on_success: bool = (
            cleanup_on_success
            if cleanup_on_success is not None
            else settings.cleanup_on_success
        )
        self._manager: WorkspaceManager = WorkspaceManager(
            root,
            clock=self._clock,
            default_quota_bytes=default_quota_bytes,
            allow_symlinks=allow_symlinks,
        )

    # ------------------------------------------------------------------ 属性

    @property
    def manager(self) -> WorkspaceManager:
        """工作区管理器（测试与诊断用）。"""
        return self._manager

    @property
    def policy_guard(self) -> CommandPolicyGuard:
        """命令策略守卫（测试与诊断用）。"""
        return self._policy

    # ------------------------------------------------------------------ prepare

    async def prepare(self, spec: WorkspaceSpec) -> WorkspaceHandle:
        """创建工作区并返回句柄。"""
        return await self._manager.create(spec, executor_kind=self.kind)

    # ------------------------------------------------------------------ run

    async def run(self, cmd: CommandSpec) -> CommandResult:
        """在工作区内执行命令。

        流程：查句柄 → 策略裁决 → 解析 ``cwd`` → 过滤环境变量 → 派生进程 →
        泵取输出 → 处理超时/退出码。

        Args:
            cmd: 命令申请单（``argv`` 数组，禁止 shell）。

        Returns:
            :class:`CommandResult`；**非零退出码不抛异常**。

        Raises:
            WorkspaceNotReadyError: ``handle_id`` 未知。
            loopforge.core.errors.PolicyDeniedError: 命中策略。
            loopforge.executors.base.PathEscapeError: ``cwd`` 逃逸。
            InvalidCommandError: ``argv`` 为空或程序不存在。
        """
        handle = self._manager.get(cmd.handle_id)
        if not cmd.argv:
            raise InvalidCommandError("argv 为空，拒绝执行", details={"handle_id": cmd.handle_id})

        await self._policy.enforce(
            cmd.argv,
            context={
                "run_id": handle.run_id,
                "task_id": handle.task_id,
                "handle_id": handle.id,
                "cwd": cmd.cwd,
            },
        )

        sandbox = self._manager.sandbox(handle)
        cwd = sandbox.resolve_dir(cmd.cwd, must_exist=True)
        program = self._resolve_program(cmd.argv[0])
        argv = [program, *cmd.argv[1:]]
        env = self._build_env(handle.env, cmd.env)

        sink = StreamSink(max_output_bytes=cmd.max_output_bytes)
        started = self._clock.monotonic()
        timed_out = False
        oom_killed = False
        exit_code = -1

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.PIPE if cmd.stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **spawn_kwargs(self._limits),
            )
            try:
                if cmd.stdin is not None:
                    if process.stdin is not None:
                        try:
                            process.stdin.write(cmd.stdin.encode("utf-8"))
                            await process.stdin.drain()
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        process.stdin.close()

                await asyncio.wait_for(
                    pump_pair(process.stdout, process.stderr, sink),
                    timeout=cmd.timeout_s,
                )
                exit_code = await process.wait()
            except (TimeoutError, asyncio.TimeoutError):
                timed_out = True
                await kill_process_tree(process)
                exit_code = -9
        except asyncio.CancelledError:
            # 上层取消：同样要把进程树杀干净，然后原样上抛。
            if "process" in locals() and process.returncode is None:
                await kill_process_tree(process)
            raise
        except OSError as exc:
            raise InvalidCommandError(
                f"无法启动程序 {cmd.argv[0]!r}：{exc}",
                details={"argv": cmd.argv, "cause": str(exc)},
            ) from exc

        duration_ms = int((self._clock.monotonic() - started) * 1000)
        _log.info(
            "local_executor_run",
            argv=cmd.display(),
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
            truncated=sink.stdout.truncated or sink.stderr.truncated,
        )
        return CommandResult(
            exit_code=exit_code,
            stdout=sink.stdout.decode(),
            stderr=sink.stderr.decode(),
            duration_ms=duration_ms,
            truncated=sink.stdout.truncated or sink.stderr.truncated,
            timed_out=timed_out,
            oom_killed=oom_killed,
        )

    # ------------------------------------------------------------------ 文件原语

    async def write_file(
        self, handle: WorkspaceHandle, path: str, content: str, *, encoding: str = "utf-8"
    ) -> FileStat:
        """原子写入文本文件（临时文件 + rename）。"""
        self._manager.get(handle.id)
        sandbox = self._manager.sandbox(handle)
        target = sandbox.ensure_parent(path)
        return await asyncio.to_thread(write_text_atomic, target, content, encoding=encoding)

    async def read_file(
        self, handle: WorkspaceHandle, path: str, *, max_bytes: int = 1_048_576
    ) -> FileContent:
        """读取文本文件，超过 ``max_bytes`` 截断。"""
        self._manager.get(handle.id)
        sandbox = self._manager.sandbox(handle)
        target = sandbox.resolve(path, must_exist=True)
        return await asyncio.to_thread(read_text_limited, target, max_bytes=max_bytes)

    async def list_dir(
        self, handle: WorkspaceHandle, path: str, *, depth: int = 1
    ) -> DirListing:
        """列举目录。"""
        self._manager.get(handle.id)
        sandbox = self._manager.sandbox(handle)
        start = sandbox.resolve_dir(path, must_exist=True)
        return await asyncio.to_thread(
            walk_listing, sandbox.root, start, depth=depth
        )

    async def stat(self, handle: WorkspaceHandle, path: str) -> FileStat:
        """采集文件元信息（供幂等探测比对）。"""
        self._manager.get(handle.id)
        sandbox = self._manager.sandbox(handle)
        target = sandbox.resolve(path, must_exist=True)
        return await asyncio.to_thread(stat_file, target)

    # ------------------------------------------------------------------ cleanup

    async def cleanup(self, handle: WorkspaceHandle) -> None:
        """销毁工作区（幂等）。"""
        await self._manager.destroy(handle)

    # ------------------------------------------------------------------ health

    async def healthcheck(self) -> ExecutorHealth:
        """健康检查；任何情况下都不抛异常。"""
        try:
            probes = {"cwd": os.getcwd()}
            return ExecutorHealth(
                kind=self.kind,
                healthy=True,
                detail="local subprocess executor ready",
                workspace_root=str(self._manager.root),
                active_workspaces=self._manager.count(),
                checked_at=self._clock.now(),
                extra=probes,
            )
        except Exception as exc:  # noqa: BLE001 - healthcheck 永不抛异常
            return ExecutorHealth(
                kind=self.kind,
                healthy=False,
                detail=f"healthcheck failed: {exc}",
                workspace_root=str(self._manager.root),
                active_workspaces=0,
                checked_at=self._clock.now(),
            )

    # ------------------------------------------------------------------ 内部

    def _resolve_program(self, argv0: str) -> str:
        """解析可执行文件。

        Args:
            argv0: 用户提供的程序名或路径。

        Returns:
            解析后的可执行文件绝对路径。

        Raises:
            InvalidCommandError: 程序不存在或不可执行。
        """
        if os.path.sep in argv0 or (os.altsep and os.altsep in argv0) or argv0.endswith((".exe", ".bat", ".cmd")):
            candidate = shutil.which(argv0)
            if candidate:
                return candidate
            raise InvalidCommandError(
                f"程序不存在：{argv0}",
                details={"argv0": argv0, "reason": "not_found"},
            )
        resolved = shutil.which(argv0)
        if resolved is None:
            raise InvalidCommandError(
                f"程序不在 PATH 中：{argv0}",
                details={"argv0": argv0, "reason": "not_in_path"},
            )
        return resolved

    def _build_env(self, workspace_env: Mapping[str, str], cmd_env: Mapping[str, str]) -> dict[str, str]:
        """构造传给子进程的环境变量（白名单求交集）。

        规则：宿主环境 + 工作区级 env + 命令级 env，三者都先过白名单；
        白名单之外的键一律丢弃。命令级 env 优先于工作区级。

        兼容性要点：**Windows 关键系统变量恒保留**（大小写不敏感匹配）。
        实测发现：配置默认白名单写的是 ``SystemRoot``，但部分 Windows 环境里
        ``os.environ`` 只有全大写的 ``SYSTEMROOT`` —— 一旦被过滤，子进程加载
        ``_overlapped``/Winsock 直接报 ``WinError 10106``，连 ``import asyncio``
        都会失败。因此 PATH / SYSTEMROOT / COMSPEC / TEMP / TMP 永远保留，
        白名单其余键按大小写不敏感匹配。

        Args:
            workspace_env: 工作区级环境变量。
            cmd_env: 命令级环境变量。

        Returns:
            过滤后的环境变量字典。
        """
        #: Windows 关键系统变量 —— 缺失会导致 DLL/Winsock 加载失败。
        always_keep: frozenset[str] = frozenset(
            {"PATH", "SYSTEMROOT", "SystemRoot", "COMSPEC", "TEMP", "TMP"}
        )
        allowed_lower: frozenset[str] = frozenset(
            key.lower() for key in self._env_allowlist
        )

        def _passes(key: str) -> bool:
            if key in always_keep:
                return True
            return key.lower() in allowed_lower

        result: dict[str, str] = {}
        for key, value in os.environ.items():
            if _passes(key):
                result[key] = value
        for mapping in (workspace_env, cmd_env):
            for key, value in mapping.items():
                if _passes(key):
                    result[key] = value
        return result
