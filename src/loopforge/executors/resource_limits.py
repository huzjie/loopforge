"""资源限额 —— 超时、内存、输出字节、进程组。

为什么必须做整组杀死
--------------------
``pytest`` 会 fork 子进程，``npm test`` 会拉起 node 守护进程。只 kill 直接子进程
会留下一地孤儿，它们继续占着工作区文件句柄，导致后续 ``cleanup()`` 在 Windows 上
直接报 ``PermissionError``。所以：

* **POSIX**：``start_new_session=True``（等价 ``setsid``）建立新进程组，
  超时后 ``os.killpg(pgid, SIGKILL)``。
* **Windows**：``CREATE_NEW_PROCESS_GROUP`` + 超时后 ``taskkill /F /T /PID``
  （``/T`` 即 tree kill）。

内存限额同理分两套：POSIX 用 ``resource.setrlimit(RLIMIT_AS)``；Windows 没有
等价的 per-process 简易接口，退化为「靠 Docker 或不限制」，并在
:class:`ResourceLimits` 上如实标注 ``memory_enforced``。
"""

from __future__ import annotations

import asyncio
import os
import subprocess  # noqa: S404 - 仅用于取平台常量，实际执行走 asyncio
import sys
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ResourceLimits",
    "OutputAccumulator",
    "posix_preexec",
    "windows_creationflags",
    "spawn_kwargs",
    "kill_process_tree",
    "memory_limits_supported",
]


def memory_limits_supported() -> bool:
    """当前平台是否支持进程级内存硬限额。"""
    return os.name == "posix"


class ResourceLimits(BaseModel):
    """单次命令执行的资源上限。"""

    model_config = ConfigDict(extra="forbid")

    timeout_s: float = Field(default=300.0, gt=0, description="墙钟超时秒数")
    max_output_bytes: int = Field(default=1_048_576, ge=0, description="stdout/stderr 各自上限")
    max_memory_mb: int = Field(default=2048, ge=0, description="0 表示不限")
    max_processes: int = Field(default=256, ge=0, description="RLIMIT_NPROC，0 表示不限")
    max_file_size_mb: int = Field(default=256, ge=0, description="RLIMIT_FSIZE，0 表示不限")
    cpu_seconds: int = Field(default=0, ge=0, description="RLIMIT_CPU，0 表示不限")

    @property
    def memory_enforced(self) -> bool:
        """内存限额在当前平台是否真正生效。"""
        return self.max_memory_mb > 0 and memory_limits_supported()

    def describe(self) -> dict[str, Any]:
        """导出用于日志与审计的限额描述。"""
        return {
            "timeout_s": self.timeout_s,
            "max_output_bytes": self.max_output_bytes,
            "max_memory_mb": self.max_memory_mb,
            "memory_enforced": self.memory_enforced,
            "max_processes": self.max_processes,
            "platform": sys.platform,
        }


class OutputAccumulator:
    """带上限的输出累加器。

    超过 ``limit`` 后**继续消费但不再保留**，这样管道不会被写端阻塞
    （否则子进程会卡在 write 上永远不退出，超时逻辑也就失效了）。

    Args:
        limit: 保留的最大字节数；``0`` 表示丢弃全部输出。

    Example:
        >>> acc = OutputAccumulator(limit=5)
        >>> acc.feed(b"abcdefgh")
        >>> acc.truncated
        True
        >>> acc.decode()
        'abcde'
    """

    __slots__ = ("_limit", "_chunks", "_size", "_total", "_truncated")

    def __init__(self, limit: int) -> None:
        self._limit: int = max(limit, 0)
        self._chunks: list[bytes] = []
        self._size: int = 0
        self._total: int = 0
        self._truncated: bool = False

    def feed(self, chunk: bytes) -> None:
        """吞入一段输出。

        Args:
            chunk: 原始字节。
        """
        if not chunk:
            return
        self._total += len(chunk)
        if self._size >= self._limit:
            self._truncated = True
            return
        room = self._limit - self._size
        if len(chunk) <= room:
            self._chunks.append(chunk)
            self._size += len(chunk)
            return
        self._chunks.append(chunk[:room])
        self._size = self._limit
        self._truncated = True

    @property
    def truncated(self) -> bool:
        """是否发生过截断。"""
        return self._truncated

    @property
    def size(self) -> int:
        """已保留字节数。"""
        return self._size

    @property
    def total_bytes(self) -> int:
        """实际流过的总字节数（含被丢弃部分）。"""
        return self._total

    def raw(self) -> bytes:
        """返回保留下来的原始字节。"""
        return b"".join(self._chunks)

    def decode(self, encoding: str = "utf-8") -> str:
        """解码为文本；非法字节用替换字符兜底，**绝不因编码问题抛异常**。

        Args:
            encoding: 目标编码。

        Returns:
            解码后的文本；发生截断时追加显式标记。
        """
        text = self.raw().decode(encoding, errors="replace")
        if self._truncated:
            dropped = self._total - self._size
            text = f"{text}\n...[输出已截断，丢弃 {dropped} 字节]"
        return text


def posix_preexec(limits: ResourceLimits) -> Callable[[], None] | None:
    """构造 POSIX ``preexec_fn``，在 exec 前套上 rlimit。

    Args:
        limits: 资源上限。

    Returns:
        可传给 ``subprocess`` 的回调；非 POSIX 平台返回 ``None``。
    """
    if os.name != "posix":
        return None
    import resource  # noqa: PLC0415 - 仅 POSIX 可用，必须延迟导入

    def _apply() -> None:
        """在子进程中生效的限额设置。"""
        if limits.max_memory_mb > 0:
            nbytes = limits.max_memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (nbytes, nbytes))
        if limits.max_file_size_mb > 0:
            fsize = limits.max_file_size_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        if limits.cpu_seconds > 0:
            resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
        if limits.max_processes > 0 and hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(
                resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes)
            )
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return _apply


def windows_creationflags() -> int:
    """Windows 进程创建标志：独立进程组 + 不弹控制台窗口。

    Returns:
        ``creationflags`` 位掩码；非 Windows 返回 ``0``。
    """
    if os.name != "nt":
        return 0
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return int(flags)


def spawn_kwargs(limits: ResourceLimits) -> dict[str, Any]:
    """生成传给 ``asyncio.create_subprocess_exec`` 的平台相关参数。

    Args:
        limits: 资源上限。

    Returns:
        关键字参数字典（POSIX 带 ``start_new_session`` 与 ``preexec_fn``；
        Windows 带 ``creationflags``）。
    """
    if os.name == "nt":
        return {"creationflags": windows_creationflags()}
    kwargs: dict[str, Any] = {"start_new_session": True}
    preexec = posix_preexec(limits)
    if preexec is not None:
        kwargs["preexec_fn"] = preexec
    return kwargs


async def kill_process_tree(process: asyncio.subprocess.Process, *, grace_s: float = 2.0) -> bool:
    """杀死整个进程树（含所有孙进程）。

    Args:
        process: ``asyncio`` 子进程句柄。
        grace_s: 先礼后兵的宽限秒数 —— 先温柔终止，超时后强杀。

    Returns:
        ``True`` 表示进程已确认退出。
    """
    if process.returncode is not None:
        return True

    if os.name == "nt":
        await _kill_tree_windows(process)
    else:
        _kill_tree_posix(process)

    try:
        await asyncio.wait_for(process.wait(), timeout=grace_s)
    except (TimeoutError, asyncio.TimeoutError):
        try:
            process.kill()
        except ProcessLookupError:  # pragma: no cover - 竞态下进程已消失
            return True
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_s)
        except (TimeoutError, asyncio.TimeoutError):  # pragma: no cover - 极端情况
            return False
    return True


async def _kill_tree_windows(process: asyncio.subprocess.Process) -> None:
    """Windows 下用 ``taskkill /F /T`` 递归杀死进程树。"""
    pid = process.pid
    try:
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/F",
            "/T",
            "/PID",
            str(pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=windows_creationflags(),
        )
        await asyncio.wait_for(killer.wait(), timeout=5.0)
    except (OSError, TimeoutError, asyncio.TimeoutError):
        # taskkill 不可用时退回单进程 kill，至少不留主进程。
        with _suppress_process_errors():
            process.kill()


def _kill_tree_posix(process: asyncio.subprocess.Process) -> None:
    """POSIX 下用 ``killpg`` 干掉整个进程组。"""
    import signal  # noqa: PLC0415 - 仅在 POSIX 分支需要

    try:
        pgid = os.getpgid(process.pid)
    except (ProcessLookupError, OSError):
        with _suppress_process_errors():
            process.kill()
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        with _suppress_process_errors():
            process.kill()
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return


class _suppress_process_errors:  # noqa: N801 - 上下文管理器惯用小写
    """吞掉进程已退出引发的异常（比 contextlib.suppress 更明确表意）。"""

    def __enter__(self) -> None:
        """无需准备。"""
        return None

    def __exit__(self, exc_type: type[BaseException] | None, *_: Any) -> bool:
        """仅吞掉 ``ProcessLookupError`` / ``OSError``。"""
        return exc_type is not None and issubclass(exc_type, ProcessLookupError | OSError)
