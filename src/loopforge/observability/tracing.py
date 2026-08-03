"""轻量追踪 —— 无 OpenTelemetry 依赖的 span 上下文。

记录每个 span 的耗时与关键字段，并写入结构化日志（``trace_span`` 上下文管理器）。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from loopforge.core.logging import get_logger

__all__ = ["TraceContext", "trace_span"]


@dataclass
class TraceContext:
    """当前追踪上下文（trace_id + span 栈）。"""

    trace_id: str = ""
    spans: list[str] = field(default_factory=list)

    def push(self, name: str) -> None:
        """压入 span 名。"""
        self.spans.append(name)

    def pop(self, name: str) -> None:
        """弹出 span 名。"""
        if self.spans and self.spans[-1] == name:
            self.spans.pop()


@contextmanager
def trace_span(
    name: str,
    *,
    trace_id: str | None = None,
    logger: Any | None = None,
    **fields: Any,
) -> Iterator[TraceContext]:
    """记录一个命名 span。

    :param name: span 名（如 ``run_service.create``）。
    :param trace_id: 关联的 trace_id。
    :param logger: 结构化 logger；缺省用模块 logger。
    :param fields: 附加字段。
    """
    log = logger or get_logger("loopforge.tracing")
    start = time.perf_counter()
    ctx = TraceContext(trace_id=trace_id or "")
    try:
        yield ctx
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log.info(
            "span",
            span=name,
            duration_ms=duration_ms,
            trace_id=trace_id or "",
            **fields,
        )
