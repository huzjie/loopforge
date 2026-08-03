"""Event / AuditLog DTO 与 WebSocket 消息封套。

对应模型：:mod:`loopforge.models.event`。

WebSocket 协议（``docs/ARCHITECTURE.md`` §7.3）：客户端带 ``last_seq`` 连上来，
服务端先回放 ``(last_seq, now]`` 的历史事件，再转入实时推送。
:class:`WsEnvelope` 就是这条通道上的唯一消息格式。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from loopforge.constants import MAX_EVENT_EXPORT_ROWS
from loopforge.core.enums import Severity
from loopforge.core.event_types import EventType
from loopforge.schemas.common import LFModel, ORMModel, PageQuery

__all__ = [
    "WS_MESSAGE_KINDS",
    "EventRead",
    "EventQuery",
    "EventExportRequest",
    "ChainVerifyResult",
    "WsEnvelope",
    "WsSubscribeRequest",
    "AuditLogRead",
    "AuditLogQuery",
]

#: WebSocket 消息种类。
WS_MESSAGE_KINDS: tuple[str, ...] = (
    "event",
    "backfill",
    "heartbeat",
    "ack",
    "error",
    "eos",
)


class EventRead(ORMModel):
    """事件读模型。"""

    seq: int = Field(ge=0, description="全序自增序号，回放与游标分页的锚点")
    id: str
    run_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    type: str
    severity: Severity = Severity.INFO
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    prev_hash: str | None = None
    hash: str = ""
    created_at: datetime

    @property
    def event_type(self) -> EventType | None:
        """把字符串 ``type`` 解析回冻结枚举。

        :returns: :class:`~loopforge.core.event_types.EventType`；
            未知类型（例如来自更高版本的对端）返回 ``None`` 而不是抛错 ——
            回放历史事件时遇到未知类型也必须能继续。
        """
        try:
            return EventType(self.type)
        except ValueError:
            return None


class EventQuery(PageQuery):
    """事件列表 / 回放查询参数。"""

    run_id: str | None = Field(default=None, max_length=26)
    task_id: str | None = Field(default=None, max_length=26)
    step_id: str | None = Field(default=None, max_length=26)
    types: list[str] = Field(default_factory=list, description="事件类型过滤，空表示不限")
    min_severity: Severity | None = Field(default=None)
    trace_id: str | None = Field(default=None, max_length=64)
    after_seq: int | None = Field(default=None, ge=0, description="游标：只取 seq 大于它的")
    before_seq: int | None = Field(default=None, ge=0)

    @field_validator("types")
    @classmethod
    def _check_types(cls, value: list[str]) -> list[str]:
        """过滤掉未知事件类型，保留已知的。

        这里刻意「宽进」：前端传了个拼错的类型，正确行为是忽略它并返回其余结果，
        而不是让整个事件面板 422 白屏。
        """
        known = {member.value for member in EventType}
        return [item for item in value if item in known]

    @model_validator(mode="after")
    def _check_seq_window(self) -> Self:
        """校验 seq 窗口不倒挂。

        :raises ValueError: 窗口非法。
        """
        if (
            self.after_seq is not None
            and self.before_seq is not None
            and self.after_seq >= self.before_seq
        ):
            raise ValueError("after_seq 必须小于 before_seq")
        return self


class EventExportRequest(LFModel):
    """事件导出请求（NDJSON / JSON 数组）。"""

    run_id: str = Field(min_length=1, max_length=26)
    format: str = Field(default="ndjson", description="ndjson / json")
    max_rows: int = Field(default=MAX_EVENT_EXPORT_ROWS, ge=1, le=MAX_EVENT_EXPORT_ROWS)
    include_payload: bool = Field(default=True)
    verify_chain: bool = Field(default=True, description="导出前先校验哈希链")

    @field_validator("format")
    @classmethod
    def _check_format(cls, value: str) -> str:
        """校验导出格式。

        :raises ValueError: 格式未知。
        """
        normalized = value.strip().lower()
        if normalized not in {"ndjson", "json"}:
            raise ValueError("format 必须是 ndjson 或 json")
        return normalized


class ChainVerifyResult(LFModel):
    """事件哈希链校验结果。"""

    run_id: str
    total: int = Field(default=0, ge=0)
    valid: bool = Field(default=True)
    broken_at_seq: int | None = Field(default=None, description="第一条断链的 seq")
    reason: str = Field(default="")

    @property
    def summary(self) -> str:
        """一句话结论，便于直接写进日志。"""
        if self.valid:
            return f"链完整：{self.total} 条事件全部通过校验"
        return f"链断裂于 seq={self.broken_at_seq}：{self.reason}"


class WsSubscribeRequest(LFModel):
    """WebSocket 订阅请求（客户端 → 服务端的第一帧）。"""

    run_id: str | None = Field(default=None, max_length=26)
    last_seq: int = Field(default=0, ge=0, description="客户端已收到的最大 seq")
    types: list[str] = Field(default_factory=list)
    min_severity: Severity | None = Field(default=None)


class WsEnvelope(LFModel):
    """WebSocket 消息封套。"""

    kind: str = Field(default="event")
    seq: int = Field(default=0, ge=0)
    event: EventRead | None = Field(default=None)
    data: dict[str, Any] = Field(default_factory=dict)
    sent_at: datetime | None = Field(default=None)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        """校验消息种类。

        :raises ValueError: 种类未知。
        """
        normalized = value.strip().lower()
        if normalized not in WS_MESSAGE_KINDS:
            raise ValueError(f"kind 必须是 {list(WS_MESSAGE_KINDS)} 之一")
        return normalized

    @model_validator(mode="after")
    def _check_payload(self) -> Self:
        """``event`` / ``backfill`` 必须携带事件体。

        :raises ValueError: 缺少事件体。
        """
        if self.kind in {"event", "backfill"} and self.event is None:
            raise ValueError(f"kind={self.kind} 必须携带 event 字段")
        return self

    @classmethod
    def of_event(cls, event: EventRead, *, backfill: bool = False) -> WsEnvelope:
        """从事件构造封套。

        :param event: 事件读模型。
        :param backfill: 是否属于历史回放。
        """
        return cls(
            kind="backfill" if backfill else "event",
            seq=event.seq,
            event=event,
        )

    @classmethod
    def of_heartbeat(cls, seq: int = 0) -> WsEnvelope:
        """构造心跳帧。"""
        return cls(kind="heartbeat", seq=seq)

    @classmethod
    def of_error(cls, code: str, message: str) -> WsEnvelope:
        """构造错误帧。"""
        return cls(kind="error", data={"code": code, "message": message})


class AuditLogRead(ORMModel):
    """审计日志读模型。"""

    id: str
    actor: str = "system"
    actor_type: str = "system"
    action: str
    resource_type: str
    resource_id: str | None = None
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    ip: str | None = None
    reason: str | None = None
    created_at: datetime


class AuditLogQuery(PageQuery):
    """审计日志查询参数。"""

    actor: str | None = Field(default=None, max_length=200)
    actor_type: str | None = Field(default=None, max_length=32)
    action: str | None = Field(default=None, max_length=100)
    resource_type: str | None = Field(default=None, max_length=64)
    resource_id: str | None = Field(default=None, max_length=26)
