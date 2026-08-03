"""检查点存储 —— Step 增量点 + 轮次一致性快照。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1 / §6.3.4 / ADR-0012。

设计要点
--------
* 每个 Phase 结束写一个 ``step`` 粒度检查点；每轮结束写 ``loop`` 粒度检查点；
  里程碑达成写 ``milestone`` 粒度检查点；
* ``kill -9`` 最多损失一个 Phase 的进度：恢复时 ``load_latest`` 拿到最近
  快照，配合 :class:`~loopforge.orchestration.replay.ReplayBuilder` 续跑；
* ``content_hash`` 用 :func:`~loopforge.core.hashing.content_hash` 计算，
  恢复时可校验快照是否被篡改；
* ``prune`` 只保留最近 ``keep`` 个，控制表体积。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.clock import get_clock
from loopforge.core.hashing import content_hash
from loopforge.core.ids import new_ulid
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork
from loopforge.models.step import Checkpoint

__all__ = ["CheckpointStore", "CheckpointSnapshot"]


class CheckpointSnapshot:
    """检查点快照的值对象（脱离 ORM 行，便于跨层传递）。"""

    __slots__ = ("checkpoint_id", "run_id", "task_id", "loop_index", "kind",
                 "snapshot", "last_step_id", "last_event_seq", "content_hash",
                 "created_at")

    def __init__(
        self,
        *,
        checkpoint_id: str,
        run_id: str,
        task_id: str | None,
        loop_index: int,
        kind: str,
        snapshot: dict[str, Any],
        last_step_id: str | None,
        last_event_seq: int,
        content_hash: str,
        created_at: Any,
    ) -> None:
        self.checkpoint_id = checkpoint_id
        self.run_id = run_id
        self.task_id = task_id
        self.loop_index = loop_index
        self.kind = kind
        self.snapshot = dict(snapshot)
        self.last_step_id = last_step_id
        self.last_event_seq = last_event_seq
        self.content_hash = content_hash
        self.created_at = created_at

    def to_dict(self) -> dict[str, Any]:
        """转成字典（回放 / 调试用）。"""
        return {
            "checkpoint_id": self.checkpoint_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "loop_index": self.loop_index,
            "kind": self.kind,
            "snapshot": self.snapshot,
            "last_step_id": self.last_step_id,
            "last_event_seq": self.last_event_seq,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
        }

    @classmethod
    def from_orm(cls, row: Checkpoint) -> "CheckpointSnapshot":
        """从 ORM 行构造快照值对象。"""
        return cls(
            checkpoint_id=row.id,
            run_id=row.run_id,
            task_id=row.task_id,
            loop_index=row.loop_index,
            kind=row.kind,
            snapshot=dict(row.snapshot or {}),
            last_step_id=row.last_step_id,
            last_event_seq=row.last_event_seq,
            content_hash=row.content_hash,
            created_at=row.created_at,
        )


class CheckpointStore:
    """检查点持久化（冻结出口）。

    :param session_factory: session 工厂；``None`` 使用全局。
    """

    def __init__(self, *, session_factory: Any | None = None) -> None:
        self._session_factory: Any = (
            session_factory if session_factory is not None else get_session_factory()
        )

    async def save(
        self,
        *,
        run_id: str,
        snapshot: dict[str, Any],
        kind: str = "loop",
        task_id: str | None = None,
        loop_index: int = 0,
        last_step_id: str | None = None,
        last_event_seq: int = 0,
    ) -> CheckpointSnapshot:
        """写一个检查点。

        :param run_id: 所属 Run。
        :param snapshot: 快照内容（跑到的轮次、阶段、上下文摘要等）。
        :param kind: ``step`` / ``loop`` / ``milestone``。
        :param task_id: 关联 Task。
        :param loop_index: 轮次。
        :param last_step_id: 最近处理的 Step。
        :param last_event_seq: 最近事件序号。
        :returns: 快照值对象。
        """
        digest = content_hash(snapshot)
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            row = Checkpoint(
                id=new_ulid(),
                run_id=run_id,
                task_id=task_id,
                loop_index=loop_index,
                kind=kind,
                snapshot=dict(snapshot),
                last_step_id=last_step_id,
                last_event_seq=last_event_seq,
                content_hash=digest,
            )
            uow.session.add(row)
            await uow.commit()
            return CheckpointSnapshot.from_orm(row)

    async def load_latest(self, run_id: str) -> CheckpointSnapshot | None:
        """取某 Run 最新的检查点。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            row = await uow.checkpoints.latest_for_run(run_id)
            return CheckpointSnapshot.from_orm(row) if row is not None else None

    async def restore(self, run_id: str) -> dict[str, Any] | None:
        """恢复某 Run 最近一次快照的内容（``None`` 表示无检查点）。

        :returns: 快照 dict。
        """
        latest = await self.load_latest(run_id)
        return dict(latest.snapshot) if latest is not None else None

    async def list_loop(
        self, run_id: str, loop_index: int, *, limit: int | None = None
    ) -> list[CheckpointSnapshot]:
        """列出某 Run 某一轮的全部检查点。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            rows = await uow.checkpoints.list_for_loop(
                run_id, loop_index, limit=limit
            )
            return [CheckpointSnapshot.from_orm(row) for row in rows]

    async def prune(self, *, run_id: str | None = None, keep: int = 10) -> int:
        """只保留每个 Run 最近 ``keep`` 个检查点，返回删除条数。

        :param run_id: 限定 Run；``None`` 表示全局清理。
        :param keep: 每个 Run 保留的最多检查点数。
        :returns: 删除的检查点行数。
        """
        from sqlalchemy import delete, select

        async with UnitOfWork(session_factory=self._session_factory) as uow:
            stmt = select(Checkpoint.run_id, Checkpoint.created_at).order_by(
                Checkpoint.run_id, Checkpoint.created_at.desc()
            )
            if run_id is not None:
                stmt = stmt.where(Checkpoint.run_id == run_id)
            rows = (await uow.session.execute(stmt)).all()
            seen: dict[str, int] = {}
            to_delete: list[str] = []
            for row_run_id, _created in rows:
                count = seen.get(row_run_id, 0)
                seen[row_run_id] = count + 1
                if count >= keep:
                    to_delete.append(row_run_id)
            if not to_delete:
                return 0
            result = await uow.session.execute(
                delete(Checkpoint).where(
                    Checkpoint.run_id.in_(to_delete),
                    Checkpoint.created_at.notin_(
                        select(Checkpoint.created_at)
                        .where(Checkpoint.run_id.in_(to_delete))
                        .order_by(Checkpoint.created_at.desc())
                        .limit(keep * max(1, len(to_delete)))
                    ),
                )
            )
            deleted = int(result.rowcount or 0)
            await uow.commit()
            return deleted
