"""事件回放 —— 由 events 表重建 ReplayFrame 序列，支持游标分页。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1 / §11.6。

冻结契约（§11.6）::

    class ReplayBuilder:
        async def frames(self, *, run_id: str, cursor: str | None = None,
                         limit: int = 100) -> CursorPage[ReplayFrame]: ...
"""

from __future__ import annotations

import base64
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork

__all__ = ["ReplayFrame", "CursorPage", "ReplayBuilder"]

T = TypeVar("T")


class ReplayFrame(BaseModel):
    """一帧回放数据。"""

    model_config = ConfigDict(extra="forbid")

    seq: int
    event_type: str
    run_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    severity: str = "INFO"
    hash: str = ""
    created_at: Any = None


class CursorPage(BaseModel, Generic[T]):
    """游标分页信封（§11.8 固定形状的子集）。"""

    model_config = ConfigDict(extra="forbid")

    items: list[T] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    total: int = 0


def _encode_cursor(seq: int) -> str:
    """把事件序号编码成不透明游标。"""
    return base64.urlsafe_b64encode(str(seq).encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str | None) -> int:
    """把游标解码成事件序号；``None`` 表示从头开始。"""
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8"))
    except Exception:  # noqa: BLE001 - 非法游标从头开始
        return 0


class ReplayBuilder:
    """从 events 表重建回放帧序列。

    :param session_factory: session 工厂；``None`` 使用全局。
    """

    def __init__(self, *, session_factory: Any | None = None) -> None:
        from loopforge.db.session import get_session_factory

        self._session_factory: Any = (
            session_factory if session_factory is not None else get_session_factory()
        )

    async def frames(
        self,
        *,
        run_id: str,
        cursor: str | None = None,
        limit: int = 100,
    ) -> CursorPage[ReplayFrame]:
        """按事件 seq 正序返回一页回放帧。

        :param run_id: 目标 Run。
        :param cursor: 不透明游标（上一页的 ``next_cursor``）。
        :param limit: 每页条数（1..200）。
        :returns: 分页结果。
        """
        from sqlalchemy import func, select

        from loopforge.models.event import Event

        page_size = max(1, min(200, limit))
        after_seq = _decode_cursor(cursor)
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            total = int(
                (
                    await uow.session.execute(
                        select(func.count())
                        .select_from(Event)
                        .where(Event.run_id == run_id)
                    )
                ).scalar_one()
            )
            rows = (
                await uow.session.execute(
                    select(Event)
                    .where(Event.run_id == run_id, Event.seq > after_seq)
                    .order_by(Event.seq)
                    .limit(page_size + 1)
                )
            ).scalars().all()
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        frames = [
            ReplayFrame(
                seq=row.seq,
                event_type=row.type,
                run_id=row.run_id,
                task_id=row.task_id,
                step_id=row.step_id,
                payload=dict(row.payload or {}),
                trace_id=row.trace_id,
                severity=str(row.severity),
                hash=row.hash,
                created_at=row.created_at,
            )
            for row in rows
        ]
        next_cursor = _encode_cursor(rows[-1].seq) if has_more and rows else None
        return CursorPage[ReplayFrame](
            items=frames,
            next_cursor=next_cursor,
            has_more=has_more,
            total=total,
        )
