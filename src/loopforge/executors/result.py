"""结果聚合 —— 执行层 DTO 的统一出口与流式结果转换。

为什么需要这个模块
------------------
``base.py`` 已冻结 :class:`CommandResult` 等 DTO，但有两个缺口：

1. 下游（WP4/WP6）经常不知道从哪个模块 import ``CommandSpec``，本模块做**一次
   再导出**，形成单一 import 面（``from loopforge.executors.result import
   CommandSpec, CommandResult, ...``）。
2. 流式执行（``stream.py``）产出的是 :class:`StreamSink`，需要一条把
   ``StreamSink`` → :class:`CommandResult` 的转换路径，这里提供
   :func:`sink_to_result`。

注意：**不新增任何字段**，全部 DTO 仍以 ``base.py`` 为准 —— 字段冻结是跨包
契约，不允许在再导出层悄悄加字段。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.clock import Clock, get_clock
from loopforge.executors.base import (
    CommandResult,
    CommandSpec,
    DirEntry,
    DirListing,
    ExecutorHealth,
    FileContent,
    FileStat,
    WorkspaceHandle,
    WorkspaceSpec,
)
from loopforge.executors.stream import StreamSink

__all__ = [
    # 再导出（与 base.py 同一对象，不是副本）
    "CommandSpec",
    "CommandResult",
    "DirEntry",
    "DirListing",
    "ExecutorHealth",
    "FileContent",
    "FileStat",
    "WorkspaceHandle",
    "WorkspaceSpec",
    # 本模块新增
    "sink_to_result",
    "make_timeout_result",
]


def sink_to_result(
    sink: StreamSink,
    *,
    exit_code: int,
    duration_ms: int,
    timed_out: bool = False,
    oom_killed: bool = False,
) -> CommandResult:
    """把 :class:`StreamSink` 汇总转成冻结的 :class:`CommandResult`。

    Args:
        sink: ``stream.py`` 泵取得到的输出汇总。
        exit_code: 进程退出码。
        duration_ms: 耗时（毫秒）。
        timed_out: 是否因超时被强杀。
        oom_killed: 是否因内存超限被杀。

    Returns:
        :class:`CommandResult`。
    """
    return CommandResult(
        exit_code=exit_code,
        stdout=sink.stdout.decode(),
        stderr=sink.stderr.decode(),
        duration_ms=duration_ms,
        truncated=sink.stdout.truncated or sink.stderr.truncated,
        timed_out=timed_out,
        oom_killed=oom_killed,
    )


def make_timeout_result(
    sink: StreamSink,
    *,
    timeout_s: float,
    clock: Clock | None = None,
    oom_killed: bool = False,
) -> CommandResult:
    """构造超时结果（进程被强杀后调用）。

    Args:
        sink: 已泵取的输出汇总。
        timeout_s: 配置的超时秒数。
        clock: 时间源（仅用于记录，不参与计算）。
        oom_killed: 是否因内存超限被杀。

    Returns:
        带 ``timed_out=True`` 的 :class:`CommandResult`。
    """
    _clock = clock or get_clock()
    _ = _clock
    return sink_to_result(
        sink,
        exit_code=-9,
        duration_ms=int(timeout_s * 1000),
        timed_out=True,
        oom_killed=oom_killed,
    )


def result_summary(result: CommandResult, *, limit: int = 4000) -> dict[str, Any]:
    """把结果压成适合喂给 LLM 的字典摘要。

    Args:
        result: 命令结果。
        limit: 文本摘要上限。

    Returns:
        结构化摘要字典。
    """
    return {
        "exit_code": result.exit_code,
        "ok": result.ok,
        "timed_out": result.timed_out,
        "oom_killed": result.oom_killed,
        "truncated": result.truncated,
        "duration_ms": result.duration_ms,
        "output": result.combined_output(limit=limit),
    }
