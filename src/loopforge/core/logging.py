"""结构化日志 —— structlog 配置、上下文注入与自动脱敏。

强制日志字段（``TASK_BREAKDOWN`` §12.4）::

    必带：ts / level / logger / event / trace_id
    有上下文时带：run_id / task_id / step_id / worker_id / role / provider / model

.. warning::
   **禁止把完整 prompt 写入日志**。需要留证时写事件表（``events.payload``），
   日志里只留摘要与哈希。本模块的脱敏处理器是最后一道防线，不是免死金牌。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from typing import Any, Final, Literal

import structlog
from structlog.contextvars import (
    bind_contextvars,
    clear_contextvars,
    get_contextvars,
    unbind_contextvars,
)
from structlog.typing import EventDict, WrappedLogger

from loopforge.core.ids import new_trace_id
from loopforge.core.redaction import redact_value

__all__ = [
    "NOTICE",
    "CONTEXT_FIELDS",
    "configure_logging",
    "get_logger",
    "bind_context",
    "unbind_context",
    "clear_context",
    "current_context",
    "get_trace_id",
    "log_context",
    "is_configured",
]

#: 自定义日志级别 NOTICE（介于 INFO 与 WARNING 之间，对齐 ``Severity``）。
NOTICE: Final[int] = 25

#: 允许绑定到 contextvar 的字段白名单。
CONTEXT_FIELDS: Final[tuple[str, ...]] = (
    "trace_id",
    "run_id",
    "task_id",
    "step_id",
    "worker_id",
    "lease_id",
    "role",
    "provider",
    "model",
    "loop_index",
)

_configured: bool = False


def is_configured() -> bool:
    """日志系统是否已初始化。

    Returns:
        已调用过 :func:`configure_logging` 返回 ``True``。
    """
    return _configured


# ---------------------------------------------------------------------------
# 处理器
# ---------------------------------------------------------------------------


def _ensure_trace_id(
    _logger: WrappedLogger, _method: str, event_dict: EventDict
) -> EventDict:
    """确保每条日志都带 ``trace_id``（缺失时现场生成）。

    Args:
        _logger: structlog 内部 logger（未使用）。
        _method: 日志方法名（未使用）。
        event_dict: 事件字典。

    Returns:
        补齐 ``trace_id`` 的事件字典。
    """
    if not event_dict.get("trace_id"):
        event_dict["trace_id"] = new_trace_id()
    return event_dict


def _rename_timestamp(
    _logger: WrappedLogger, _method: str, event_dict: EventDict
) -> EventDict:
    """把 structlog 的 ``timestamp`` 字段重命名为约定的 ``ts``。

    Args:
        _logger: structlog 内部 logger（未使用）。
        _method: 日志方法名（未使用）。
        event_dict: 事件字典。

    Returns:
        重命名后的事件字典。
    """
    if "timestamp" in event_dict:
        event_dict["ts"] = event_dict.pop("timestamp")
    return event_dict


def _redact_processor(
    _logger: WrappedLogger, _method: str, event_dict: EventDict
) -> EventDict:
    """对整个事件字典执行递归脱敏。

    Args:
        _logger: structlog 内部 logger（未使用）。
        _method: 日志方法名（未使用）。
        event_dict: 事件字典。

    Returns:
        脱敏后的事件字典（原地替换值，键不变）。
    """
    for key in list(event_dict.keys()):
        if key in {"ts", "level", "logger", "event"}:
            continue
        event_dict[key] = redact_value(event_dict[key], key=key, _depth=1)
    return event_dict


def _drop_prompt_bodies(
    _logger: WrappedLogger, _method: str, event_dict: EventDict
) -> EventDict:
    """硬性剔除疑似完整 prompt 的超长字段（§12.4）。

    超过 2000 字符的字符串会被截断并标注原长度。

    Args:
        _logger: structlog 内部 logger（未使用）。
        _method: 日志方法名（未使用）。
        event_dict: 事件字典。

    Returns:
        截断后的事件字典。
    """
    limit = 2000
    for key, value in list(event_dict.items()):
        if key == "event":
            continue
        if isinstance(value, str) and len(value) > limit:
            event_dict[key] = f"{value[:limit]}…[truncated {len(value)} chars]"
    return event_dict


def _add_severity(_logger: WrappedLogger, method: str, event_dict: EventDict) -> EventDict:
    """把 structlog 方法名映射成大写 ``level`` 字段。

    Args:
        _logger: structlog 内部 logger（未使用）。
        method: 日志方法名。
        event_dict: 事件字典。

    Returns:
        含 ``level`` 的事件字典。
    """
    event_dict.setdefault("level", method.upper())
    return event_dict


# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------


def configure_logging(
    *,
    log_format: Literal["json", "console"] = "json",
    log_level: str = "INFO",
    redaction_enabled: bool = True,
    stream: Any = None,
    force: bool = False,
) -> None:
    """初始化 structlog 与标准库 logging。

    幂等：重复调用默认直接返回，除非 ``force=True``。

    Args:
        log_format: ``json`` 落盘 / ``console`` 彩色人读。
        log_level: 日志级别名，支持自定义的 ``NOTICE``。
        redaction_enabled: 是否启用脱敏处理器（生产环境务必开启）。
        stream: 输出流；``None`` 时用 :data:`sys.stdout`。
        force: 是否强制重新配置。

    Raises:
        ValueError: ``log_level`` 不是合法级别名。
    """
    global _configured  # noqa: PLW0603 —— 全局初始化开关
    if _configured and not force:
        return

    logging.addLevelName(NOTICE, "NOTICE")
    level_name = log_level.strip().upper()
    level = logging.getLevelName(level_name)
    if not isinstance(level, int):
        raise ValueError(f"未知日志级别 {log_level!r}")

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        _add_severity,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _rename_timestamp,
        _ensure_trace_id,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        _drop_prompt_bodies,
    ]
    if redaction_enabled:
        processors.append(_redact_processor)

    if log_format == "console":
        processors.append(structlog.dev.ConsoleRenderer(colors=stream is None))
    else:
        processors.append(structlog.processors.JSONRenderer(ensure_ascii=False, sort_keys=True))

    logging.basicConfig(
        format="%(message)s",
        stream=stream if stream is not None else sys.stdout,
        level=level,
        force=True,
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    # 收敛第三方库噪音。
    for noisy in ("asyncio", "aiosqlite", "sqlalchemy.engine.Engine", "urllib3", "httpx"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """取得一个绑定日志器。

    未初始化时会以默认参数自动初始化，保证库被直接 import 时也能打日志。

    Args:
        name: 日志器名，通常传 ``__name__``。

    Returns:
        structlog 绑定日志器。
    """
    if not _configured:
        configure_logging()
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name or "loopforge")
    return logger


# ---------------------------------------------------------------------------
# 上下文
# ---------------------------------------------------------------------------


def bind_context(**fields: Any) -> None:
    """把上下文字段绑定到当前协程/线程。

    只有 :data:`CONTEXT_FIELDS` 白名单里的键会被接受，其余静默忽略，
    避免上下文被业务字段污染。

    Args:
        **fields: 待绑定字段。
    """
    allowed = {key: value for key, value in fields.items() if key in CONTEXT_FIELDS}
    if allowed:
        bind_contextvars(**allowed)


def unbind_context(*keys: str) -> None:
    """解绑指定上下文字段。

    Args:
        *keys: 字段名。
    """
    valid = [key for key in keys if key in CONTEXT_FIELDS]
    if valid:
        unbind_contextvars(*valid)


def clear_context() -> None:
    """清空全部上下文字段。"""
    clear_contextvars()


def current_context() -> dict[str, Any]:
    """读取当前上下文快照。

    Returns:
        字段字典副本。
    """
    snapshot: MutableMapping[str, Any] = get_contextvars()
    return dict(snapshot)


def get_trace_id() -> str:
    """取当前 trace_id，不存在则生成并绑定。

    Returns:
        26 位 ULID 形式的 trace_id。
    """
    existing = current_context().get("trace_id")
    if isinstance(existing, str) and existing:
        return existing
    trace_id = new_trace_id()
    bind_contextvars(trace_id=trace_id)
    return trace_id


@contextmanager
def log_context(**fields: Any) -> Iterator[str]:
    """在 ``with`` 块内临时绑定日志上下文，退出时精确还原。

    Args:
        **fields: 待绑定字段；未传 ``trace_id`` 时自动生成。

    Yields:
        本次上下文的 ``trace_id``。

    Examples:
        >>> with log_context(run_id="01J...") as trace_id:
        ...     isinstance(trace_id, str)
        True
    """
    previous = current_context()
    payload = dict(fields)
    payload.setdefault("trace_id", previous.get("trace_id") or new_trace_id())
    bind_context(**payload)
    try:
        yield str(payload["trace_id"])
    finally:
        touched = [key for key in payload if key in CONTEXT_FIELDS]
        if touched:
            unbind_contextvars(*touched)
        restore = {key: value for key, value in previous.items() if key in touched}
        if restore:
            bind_contextvars(**restore)
