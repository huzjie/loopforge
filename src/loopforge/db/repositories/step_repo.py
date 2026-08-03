"""Step / Checkpoint / IdempotencyRecord / LlmCall / ToolCall / Artifact 仓储。

幂等三段式在这里落地：

* :meth:`StepRepository.record_intent` —— 写 ``INTENT_RECORDED``（唯一键冲突即重复）；
* :meth:`StepRepository.confirm` —— 副作用成功后置 ``CONFIRMED``；
* :meth:`StepRepository.list_unconfirmed` —— 崩溃恢复时捞出「意图已记录、结果未知」。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from loopforge.core.clock import get_clock
from loopforge.core.enums import SideEffectClass, StepState
from loopforge.core.errors import IdempotencyConflictError
from loopforge.core.ids import new_ulid
from loopforge.core.redaction import redact_value
from loopforge.db.repositories.base import BaseRepository, clamp_limit
from loopforge.models.artifact import Artifact
from loopforge.models.call import LlmCall, ToolCall
from loopforge.models.step import Checkpoint, IdempotencyRecord, Step

__all__ = [
    "StepRepository",
    "CheckpointRepository",
    "IdempotencyRepository",
    "LlmCallRepository",
    "ToolCallRepository",
    "ArtifactRepository",
]


class StepRepository(BaseRepository[Step]):
    """Step 持久化 —— 幂等执行单元的生命周期。"""

    model = Step

    async def record_intent(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        phase: str,
        task_id: str | None = None,
        loop_index: int = 0,
        role: Any = None,
        side_effect_class: SideEffectClass = SideEffectClass.PURE,
        intent_payload: dict[str, Any] | None = None,
        fence_token: int = 0,
        attempt: int = 0,
        now: datetime | None = None,
    ) -> Step:
        """记录执行意图。

        ``idempotency_key`` 全局唯一：并发或重放场景下第二次插入会撞
        ``uq_steps_idempotency_key``，此时抛
        :class:`~loopforge.core.errors.IdempotencyConflictError`，
        调用方据此走「读取已有 Step 并复用其结果」的分支。

        :raises IdempotencyConflictError: 幂等键已存在。
        """
        moment = now if now is not None else get_clock().now()
        step = Step(
            id=new_ulid(),
            run_id=run_id,
            task_id=task_id,
            loop_index=loop_index,
            phase=phase,
            role=role,
            state=StepState.INTENT_RECORDED,
            idempotency_key=idempotency_key,
            side_effect_class=side_effect_class,
            intent_payload=redact_value(intent_payload or {}),
            result_payload={},
            attempt=attempt,
            fence_token=fence_token,
            intent_at=moment,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(step)
                await self.session.flush()
        except IntegrityError as exc:
            raise IdempotencyConflictError(
                "幂等键已存在，该意图此前已被记录",
                details={"idempotency_key": idempotency_key, "run_id": run_id},
            ) from exc
        return step

    async def get_by_idempotency_key(self, key: str) -> Step | None:
        """按幂等键取 Step。"""
        result = await self.session.execute(
            select(Step).where(Step.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def confirm(
        self,
        step_id: str,
        *,
        result_payload: dict[str, Any] | None = None,
        external_ref: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """把 Step 从 ``INTENT_RECORDED`` 确认为 ``CONFIRMED``。

        :returns: 是否确实确认了一条未确认 Step。
        """
        moment = now if now is not None else get_clock().now()
        values: dict[str, Any] = {
            "state": StepState.CONFIRMED,
            "confirmed_at": moment,
            "updated_at": moment,
            "result_payload": redact_value(result_payload or {}),
        }
        if external_ref is not None:
            values["external_ref"] = external_ref
        result = await self.session.execute(
            update(Step)
            .where(Step.id == step_id, Step.state == StepState.INTENT_RECORDED)
            .values(**values)
        )
        return int(result.rowcount or 0) == 1

    async def mark_failed(
        self,
        step_id: str,
        *,
        error_code: str,
        result_payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """把 Step 标记为 FAILED。"""
        moment = now if now is not None else get_clock().now()
        result = await self.session.execute(
            update(Step)
            .where(Step.id == step_id, Step.state == StepState.INTENT_RECORDED)
            .values(
                state=StepState.FAILED,
                error_code=error_code,
                result_payload=redact_value(result_payload or {}),
                updated_at=moment,
            )
        )
        return int(result.rowcount or 0) == 1

    async def mark_uncertain(
        self, step_id: str, *, reason: str = "", now: datetime | None = None
    ) -> bool:
        """把 Step 标记为 UNCERTAIN（副作用是否生效无法判定，需人工介入）。"""
        moment = now if now is not None else get_clock().now()
        result = await self.session.execute(
            update(Step)
            .where(Step.id == step_id, Step.state == StepState.INTENT_RECORDED)
            .values(
                state=StepState.UNCERTAIN,
                result_payload={"uncertain_reason": reason},
                updated_at=moment,
            )
        )
        return int(result.rowcount or 0) == 1

    async def list_unconfirmed(
        self, *, run_id: str | None = None, limit: int | None = None
    ) -> list[Step]:
        """列出未确认 Step（命中 ``ix_steps_unconfirmed``），恢复流程的输入。"""
        stmt = select(Step).where(Step.state == StepState.INTENT_RECORDED)
        if run_id is not None:
            stmt = stmt.where(Step.run_id == run_id)
        stmt = stmt.order_by(Step.intent_at).limit(clamp_limit(limit))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_task(
        self, task_id: str, *, loop_index: int | None = None, limit: int | None = None
    ) -> list[Step]:
        """列出某 Task 的 Step（命中 ``ix_steps_task_loop``）。"""
        stmt = select(Step).where(Step.task_id == task_id)
        if loop_index is not None:
            stmt = stmt.where(Step.loop_index == loop_index)
        stmt = stmt.order_by(Step.created_at).limit(clamp_limit(limit))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class CheckpointRepository(BaseRepository[Checkpoint]):
    """Checkpoint 持久化。"""

    model = Checkpoint

    async def latest_for_run(self, run_id: str) -> Checkpoint | None:
        """取某 Run 最新的检查点（命中 ``ix_checkpoints_latest``）。"""
        result = await self.session.execute(
            select(Checkpoint)
            .where(Checkpoint.run_id == run_id)
            .order_by(Checkpoint.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def list_for_loop(
        self, run_id: str, loop_index: int, *, limit: int | None = None
    ) -> list[Checkpoint]:
        """列出某 Run 某一轮的检查点。"""
        stmt = (
            select(Checkpoint)
            .where(Checkpoint.run_id == run_id, Checkpoint.loop_index == loop_index)
            .order_by(Checkpoint.created_at)
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class IdempotencyRepository(BaseRepository[IdempotencyRecord]):
    """幂等记录持久化（主键是 ``key`` 而非 ``id``）。"""

    model = IdempotencyRecord
    pk_field = "key"

    async def put_if_absent(
        self,
        key: str,
        *,
        scope: str = "api",
        request_fingerprint: str = "",
        response_snapshot: dict[str, Any] | None = None,
        step_id: str | None = None,
        external_ref: str | None = None,
        expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> tuple[IdempotencyRecord, bool]:
        """插入幂等记录；已存在则原样返回旧记录。

        :returns: ``(record, created)``，``created=False`` 表示命中了已有记录。
        """
        existing = await self.get(key)
        if existing is not None:
            return existing, False
        moment = now if now is not None else get_clock().now()
        record = IdempotencyRecord(
            key=key,
            scope=scope,
            step_id=step_id,
            external_ref=external_ref,
            response_snapshot=redact_value(response_snapshot or {}),
            request_fingerprint=request_fingerprint,
            created_at=moment,
            expires_at=expires_at,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(record)
                await self.session.flush()
        except IntegrityError:
            # 并发插入：另一个协程刚写进去，读回来即可。
            found = await self.get(key)
            if found is None:
                raise
            return found, False
        return record, True

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        """清理过期的幂等记录。

        :returns: 删除行数。
        """
        from sqlalchemy import delete

        moment = now if now is not None else get_clock().now()
        result = await self.session.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.expires_at.is_not(None),
                IdempotencyRecord.expires_at <= moment,
            )
        )
        return int(result.rowcount or 0)


class LlmCallRepository(BaseRepository[LlmCall]):
    """LLM 调用记录持久化（写入前强制脱敏）。"""

    model = LlmCall

    async def record(self, **values: Any) -> LlmCall:
        """落一条 LLM 调用记录，``request`` / ``response`` 自动脱敏。"""
        payload = dict(values)
        if "request" in payload:
            payload["request"] = redact_value(payload["request"])
        if "response" in payload:
            payload["response"] = redact_value(payload["response"])
        return await self.create(**payload)

    async def total_cost_for_run(self, run_id: str) -> float:
        """某 Run 的 LLM 总花费（经 steps 关联）。

        :returns: 美元金额，无记录时为 ``0.0``。
        """
        stmt = (
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0))
            .select_from(LlmCall)
            .join(Step, Step.id == LlmCall.step_id)
            .where(Step.run_id == run_id)
        )
        result = await self.session.execute(stmt)
        return float(result.scalar_one() or 0)

    async def list_by_step(self, step_id: str, *, limit: int | None = None) -> list[LlmCall]:
        """列出某 Step 下的 LLM 调用。"""
        stmt = (
            select(LlmCall)
            .where(LlmCall.step_id == step_id)
            .order_by(LlmCall.created_at)
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class ToolCallRepository(BaseRepository[ToolCall]):
    """工具调用记录持久化（写入前强制脱敏）。"""

    model = ToolCall

    async def record(self, **values: Any) -> ToolCall:
        """落一条工具调用记录，``args`` / ``result`` 自动脱敏。"""
        payload = dict(values)
        if "args" in payload:
            payload["args"] = redact_value(payload["args"])
        if "result" in payload:
            payload["result"] = redact_value(payload["result"])
        return await self.create(**payload)

    async def list_by_step(self, step_id: str, *, limit: int | None = None) -> list[ToolCall]:
        """列出某 Step 下的工具调用。"""
        stmt = (
            select(ToolCall)
            .where(ToolCall.step_id == step_id)
            .order_by(ToolCall.created_at)
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class ArtifactRepository(BaseRepository[Artifact]):
    """产物元数据持久化。"""

    model = Artifact

    async def list_by_run(
        self, run_id: str, *, kind: str | None = None, limit: int | None = None
    ) -> list[Artifact]:
        """列出某 Run 的产物。"""
        stmt = select(Artifact).where(Artifact.run_id == run_id)
        if kind is not None:
            stmt = stmt.where(Artifact.kind == kind)
        stmt = stmt.order_by(Artifact.created_at.desc()).limit(clamp_limit(limit))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_sha(self, run_id: str, sha256: str) -> Artifact | None:
        """按内容哈希去重查询（``uq_artifacts_sha_run``）。"""
        result = await self.session.execute(
            select(Artifact).where(Artifact.run_id == run_id, Artifact.sha256 == sha256)
        )
        return result.scalar_one_or_none()
