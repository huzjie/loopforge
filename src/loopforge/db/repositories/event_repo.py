"""Event / AuditLog 仓储 —— **append-only**，只提供 append 与 read。

哈希链：每次 append 都会读取该 Run 的最后一条事件哈希作为 ``prev_hash``，
再算出本条 ``hash``。:meth:`EventRepository.verify_chain` 可以对整条链做完整性
校验，任何一处篡改都会让后续全部哈希对不上。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from loopforge.constants import MAX_EVENT_EXPORT_ROWS
from loopforge.core.clock import get_clock
from loopforge.core.enums import Severity
from loopforge.core.errors import DatabaseError
from loopforge.core.event_types import EventType
from loopforge.core.ids import new_ulid
from loopforge.core.redaction import redact_value
from loopforge.db.repositories.base import BaseRepository, clamp_limit
from loopforge.models.event import AuditLog, Event

__all__ = ["EventRepository", "AuditLogRepository"]


class EventRepository(BaseRepository[Event]):
    """事件流仓储。**禁止 update / delete。**"""

    model = Event
    pk_field = "seq"

    # -- 写（唯一入口） -----------------------------------------------------

    async def append(
        self,
        event_type: EventType | str,
        payload: dict[str, Any] | None = None,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        step_id: str | None = None,
        severity: Severity = Severity.INFO,
        trace_id: str | None = None,
        now: datetime | None = None,
    ) -> Event:
        """追加一条事件并维护哈希链。

        :param event_type: :class:`~loopforge.core.event_types.EventType` 或其字符串值。
        :param payload: 事件负载，会自动脱敏。
        :returns: 已 flush 的事件（``seq`` 已由数据库分配）。
        """
        moment = now if now is not None else get_clock().now()
        prev_hash = await self.last_hash(run_id)
        event = Event(
            id=new_ulid(),
            run_id=run_id,
            task_id=task_id,
            step_id=step_id,
            type=str(event_type),
            severity=severity,
            payload=redact_value(payload or {}),
            trace_id=trace_id,
            prev_hash=prev_hash,
            created_at=moment,
        )
        event.hash = event.compute_hash()
        self.session.add(event)
        await self.session.flush()
        return event

    async def last_hash(self, run_id: str | None) -> str | None:
        """取某 Run 最后一条事件的哈希；无事件时返回 ``None``（链的创世点）。"""
        stmt = select(Event.hash).order_by(Event.seq.desc()).limit(1)
        if run_id is not None:
            stmt = select(Event.hash).where(Event.run_id == run_id)
            stmt = stmt.order_by(Event.seq.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def last_seq(self, run_id: str | None = None) -> int:
        """取最新序号；无事件时返回 ``0``。"""
        stmt = select(func.max(Event.seq))
        if run_id is not None:
            stmt = stmt.where(Event.run_id == run_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    # -- 读 -----------------------------------------------------------------

    async def list_for_run(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        types: Sequence[EventType | str] | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        """按 seq 升序分页读取某 Run 的事件（命中 ``ix_events_run_seq``）。

        :param after_seq: 只取 ``seq > after_seq`` 的事件，用于增量拉取。
        :param types: 事件类型过滤。
        """
        stmt = select(Event).where(Event.run_id == run_id, Event.seq > after_seq)
        if types:
            stmt = stmt.where(Event.type.in_([str(item) for item in types]))
        stmt = stmt.order_by(Event.seq).limit(clamp_limit(limit))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_trace(self, trace_id: str, *, limit: int | None = None) -> list[Event]:
        """按 trace_id 串起一次请求涉及的全部事件。"""
        stmt = (
            select(Event)
            .where(Event.trace_id == trace_id)
            .order_by(Event.seq)
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_run(self, run_id: str) -> int:
        """某 Run 的事件总数。"""
        result = await self.session.execute(
            select(func.count()).select_from(Event).where(Event.run_id == run_id)
        )
        return int(result.scalar_one())

    async def export_for_run(self, run_id: str, *, max_rows: int = MAX_EVENT_EXPORT_ROWS) -> list[Event]:
        """一次性导出某 Run 的全部事件（供离线回放与审计）。

        :raises DatabaseError: 事件数超过 ``max_rows`` 上限。
        """
        total = await self.count_for_run(run_id)
        if total > max_rows:
            raise DatabaseError(
                "事件数超过单次导出上限，请改用分页拉取",
                details={"run_id": run_id, "total": total, "max_rows": max_rows},
            )
        stmt = select(Event).where(Event.run_id == run_id).order_by(Event.seq)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def verify_chain(self, run_id: str) -> tuple[bool, int | None]:
        """校验某 Run 的事件哈希链完整性。

        :returns: ``(是否完好, 首个损坏事件的 seq)``；完好时第二项为 ``None``。
        """
        stmt = select(Event).where(Event.run_id == run_id).order_by(Event.seq)
        result = await self.session.execute(stmt)
        expected_prev: str | None = None
        for event in result.scalars():
            if event.prev_hash != expected_prev or not event.verify():
                return False, int(event.seq)
            expected_prev = event.hash
        return True, None


class AuditLogRepository(BaseRepository[AuditLog]):
    """审计日志仓储。**禁止 update / delete。**"""

    model = AuditLog

    async def append(
        self,
        *,
        action: str,
        resource_type: str,
        actor: str = "system",
        actor_type: str = "system",
        resource_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        trace_id: str | None = None,
        ip: str | None = None,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> AuditLog:
        """追加一条审计记录（``before`` / ``after`` 自动脱敏）。"""
        moment = now if now is not None else get_clock().now()
        entry = AuditLog(
            id=new_ulid(),
            actor=actor,
            actor_type=actor_type,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before=redact_value(before or {}),
            after=redact_value(after or {}),
            trace_id=trace_id,
            ip=ip,
            reason=reason,
            created_at=moment,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_for_resource(
        self, resource_type: str, resource_id: str, *, limit: int | None = None
    ) -> list[AuditLog]:
        """列出某资源的审计历史（命中 ``ix_audit_resource``）。"""
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.resource_type == resource_type,
                AuditLog.resource_id == resource_id,
            )
            .order_by(AuditLog.created_at.desc())
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_actor(self, actor: str, *, limit: int | None = None) -> list[AuditLog]:
        """列出某操作者的审计历史（命中 ``ix_audit_actor_created``）。"""
        stmt = (
            select(AuditLog)
            .where(AuditLog.actor == actor)
            .order_by(AuditLog.created_at.desc())
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
