"""流式输出捕获 —— 执行器的输出管道泵。

为什么需要独立成模块
--------------------
``LocalSubprocessExecutor`` 与 ``DockerExecutor`` 都要做同一件事：**一边消费
子进程/容器的 stdout/stderr，一边把字节喂给带上限的累加器**。不消费管道会导致
写端阻塞（子进程卡在 write 上永不退出，超时逻辑失效）；消费了不截断则会把
10MB 日志塞进 LLM 上下文。因此「消费 + 截断 + 可选回调」三个职责在这里一次实现。

设计
----
:class:`StreamPump` 是核心原语：

* ``pump`` 异步读取 ``asyncio.StreamReader`` 直到 EOF，逐块喂给
  :class:`~loopforge.executors.resource_limits.OutputAccumulator`。
* 可注册 ``on_chunk`` 回调（用于逐行推送给 UI / SSE），回调**不得阻塞**——
  内部用 ``asyncio.create_task`` 调度，异常只记日志。
* 读到的字节超过 ``max_bytes`` 后仍继续消费（防止写端阻塞），但不再保留。

线程安全：本模块只供 async 上下文使用，不承诺跨线程安全。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loopforge.core.logging import get_logger
from loopforge.executors.resource_limits import OutputAccumulator

__all__ = [
    "CHUNK_SIZE",
    "StreamPump",
    "pump_pair",
    "StreamSink",
]

_log = get_logger(__name__)

#: 单次从管道读取的字节数（64 KiB）。
CHUNK_SIZE: int = 65536

#: 逐行回调的签名：``async def on_line(stream: str, line: str) -> None``。
LineCallback = Callable[[str, str], Awaitable[None]]


class StreamSink:
    """一次命令执行的输出汇总。

    Attributes:
        stdout: stdout 累加器。
        stderr: stderr 累加器。
        line_events: 收到的逐行事件（含截断标记），用于测试与调试。
    """

    __slots__ = ("stdout", "stderr", "line_events")

    def __init__(self, max_output_bytes: int) -> None:
        """初始化两个累加器。

        Args:
            max_output_bytes: stdout / stderr 各自保留的字节上限。
        """
        self.stdout: OutputAccumulator = OutputAccumulator(limit=max_output_bytes)
        self.stderr: OutputAccumulator = OutputAccumulator(limit=max_output_bytes)
        self.line_events: list[tuple[str, str]] = []

    def append_line(self, stream: str, line: str) -> None:
        """记录一行事件（供 SSE / 调试消费）。"""
        if len(self.line_events) < 10_000:
            self.line_events.append((stream, line))

    def describe(self) -> dict[str, Any]:
        """导出摘要（日志 / 审计用）。"""
        return {
            "stdout_bytes": self.stdout.total_bytes,
            "stderr_bytes": self.stderr.total_bytes,
            "stdout_truncated": self.stdout.truncated,
            "stderr_truncated": self.stderr.truncated,
        }


class StreamPump:
    """从 ``asyncio.StreamReader`` 泵取字节到 :class:`OutputAccumulator`。

    Args:
        reader: 子进程的 stdout 或 stderr 管道。
        sink: 输出汇总（决定累加器与行事件归属）。
        stream_name: ``"stdout"`` 或 ``"stderr"``。
        on_line: 可选的逐行回调（异步）；仅在按 ``\\n`` 切出完整行时触发。
        decode_errors: 解码失败时的策略，默认 ``replace`` 绝不抛异常。
    """

    __slots__ = (
        "_reader",
        "_sink",
        "_stream_name",
        "_on_line",
        "_decode_errors",
        "_buffer",
    )

    def __init__(
        self,
        reader: asyncio.StreamReader,
        sink: StreamSink,
        *,
        stream_name: str,
        on_line: LineCallback | None = None,
        decode_errors: str = "replace",
    ) -> None:
        self._reader: asyncio.StreamReader = reader
        self._sink: StreamSink = sink
        self._stream_name: str = stream_name
        self._on_line: LineCallback | None = on_line
        self._decode_errors: str = decode_errors
        self._buffer: bytes = b""

    async def pump(self) -> None:
        """读取直到 EOF，边读边喂累加器。

        Returns:
            无；所有结果都在 :attr:`StreamSink` 上。
        """
        accumulator = (
            self._sink.stdout if self._stream_name == "stdout" else self._sink.stderr
        )
        while True:
            chunk = await self._reader.read(CHUNK_SIZE)
            if not chunk:
                break
            accumulator.feed(chunk)
            if self._on_line is not None:
                self._dispatch_lines(chunk)

        # 冲刷残留的半行（无换行结尾的末尾内容）。
        if self._buffer and self._on_line is not None:
            try:
                line = self._buffer.decode("utf-8", errors=self._decode_errors)
            except Exception:  # noqa: BLE001 - 解码兜底，绝不阻塞管道
                line = self._buffer.decode("utf-8", errors="replace")
            self._sink.append_line(self._stream_name, line)
            self._safe_dispatch(self._stream_name, line)
            self._buffer = b""

    def _dispatch_lines(self, chunk: bytes) -> None:
        """把 chunk 按 ``\\n`` 切行并触发回调。"""
        self._buffer += chunk
        while True:
            index = self._buffer.find(b"\n")
            if index < 0:
                break
            raw = self._buffer[:index]
            self._buffer = self._buffer[index + 1 :]
            line = raw.decode("utf-8", errors=self._decode_errors)
            self._sink.append_line(self._stream_name, line)
            self._safe_dispatch(self._stream_name, line)

    def _safe_dispatch(self, stream_name: str, line: str) -> None:
        """异步调度回调；异常只记日志，不中断泵取。"""
        if self._on_line is None:
            return

        async def _run() -> None:
            try:
                await self._on_line(stream_name, line)
            except Exception as exc:  # noqa: BLE001 - 回调失败不影响执行
                _log.warning("stream_callback_failed", stream=stream_name, error=str(exc))

        asyncio.create_task(_run())


async def pump_pair(
    stdout: asyncio.StreamReader,
    stderr: asyncio.StreamReader,
    sink: StreamSink,
    *,
    stdout_line: LineCallback | None = None,
    stderr_line: LineCallback | None = None,
) -> None:
    """同时泵取 stdout 与 stderr，等待两者都读完。

    Args:
        stdout: stdout 管道。
        stderr: stderr 管道。
        sink: 输出汇总。
        stdout_line: stdout 逐行回调。
        stderr_line: stderr 逐行回调。
    """
    pumps = [
        StreamPump(
            stdout,
            sink,
            stream_name="stdout",
            on_line=stdout_line,
        ).pump(),
        StreamPump(
            stderr,
            sink,
            stream_name="stderr",
            on_line=stderr_line,
        ).pump(),
    ]
    await asyncio.gather(*pumps)
