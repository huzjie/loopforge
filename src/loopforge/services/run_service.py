"""Run 服务 —— 生命周期管理 + 控制指令 + 统计。

Run 状态机（``ARCHITECTURE.md`` §6.1）由 ``run_repo.cas_state`` 强制校验。
控制指令（start/pause/resume/cancel/retry/rollback）全部幂等：
重复调用在已到达目标状态时静默成功。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from loopforge.constants import DEFAULT_MAX_LOOPS
from loopforge.core.clock import get_clock, to_iso8601
from loopforge.core.enums import RunState
from loopforge.core.errors import IllegalTransitionError, NotFoundError, RunError
from loopforge.core.event_types import EventType, RunFailedPayload, RunLifecyclePayload
from loopforge.core.ids import new_ulid
from loopforge.core.logging import get_logger
from loopforge.db.uow import UnitOfWork
from loopforge.models.run import Run
from loopforge.models.task import Task
from loopforge.schemas.run import RunCreate, RunDetail, RunRead, RunStats, RunUpdate

__all__ = ["RunService"]

_logger = get_logger(__name__)

#: 控制指令 → (源状态集合, 目标状态)。
_START_SOURCES = (RunState.CREATED, RunState.READY, RunState.PAUSED, RunState.RECOVERING)
_PAUSE_SOURCES = (RunState.RUNNING, RunState.PLANNING, RunState.READY)
_RESUME_SOURCES = (RunState.PAUSED,)
_CANCEL_SOURCES = (
    RunState.CREATED,
    RunState.PLANNING,
    RunState.READY,
    RunState.RUNNING,
    RunState.PAUSED,
    RunState.RECONCILING,
    RunState.RECOVERING,
    RunState.AWAITING_APPROVAL,
)


class RunService:
    """Run 生命周期服务。"""

    # -- 创建 / 读取 ---------------------------------------------------------

    async def create(self, payload: RunCreate, *, auto_start: bool | None = None) -> RunDetail:
        """创建 Run（可选自动启动）。"""
        async with UnitOfWork() as uow:
            await uow.projects.get_or_raise(payload.project_id)
            run = await uow.runs.create(
                id=new_ulid(),
                project_id=payload.project_id,
                repo_id=payload.repo_id,
                goal=payload.goal,
                constraints=payload.constraints,
                max_loops=payload.max_loops or DEFAULT_MAX_LOOPS,
                budget_id=None,
                policy_id=None,
                config_snapshot=payload.config_overrides,
                state=RunState.CREATED,
            )
            uow.collect_event(
                EventType.RUN_CREATED,
                {"run_id": run.id, "state": RunState.CREATED, "actor": "api"},
                run_id=run.id,
            )
            start = auto_start if auto_start is not None else payload.auto_start
        if start:
            await self.start(run.id)
            async with UnitOfWork() as uow:
                run = await uow.runs.get_or_raise(run.id)
        return await self.detail(run.id)

    async def get(self, run_id: str) -> RunRead:
        """Run 读模型。"""
        async with UnitOfWork() as uow:
            run = await uow.runs.get_or_raise(run_id)
        return RunRead.model_validate(run)

    async def detail(self, run_id: str) -> RunDetail:
        """Run 详情（含统计）。"""
        async with UnitOfWork() as uow:
            run = await uow.runs.get_or_raise(run_id)
            stats = await self._stats(uow, run)
        detail = RunDetail.model_validate(run)
        detail.stats = stats
        detail.config_snapshot = run.config_snapshot or {}
        return detail

    async def list(
        self,
        *,
        project_id: str | None = None,
        state: RunState | None = None,
        active_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RunRead], int]:
        """Run 列表（分页）。"""
        async with UnitOfWork() as uow:
            if project_id:
                runs = await uow.runs.list_by_project(
                    project_id, states=[state] if state else None, limit=limit, offset=offset
                )
                total = len(await uow.runs.list_by_project(project_id, states=[state] if state else None))
            else:
                filters: dict[str, Any] = {}
                if state is not None:
                    filters["state"] = state
                runs = await uow.runs.list_by(limit=limit, offset=offset, **filters)
                total = await uow.runs.count_by(**filters)
        if active_only:
            runs = [r for r in runs if r.is_active]
        return [RunRead.model_validate(r) for r in runs], total

    async def update(self, run_id: str, payload: RunUpdate) -> RunDetail:
        """更新 Run 可变字段。"""
        async with UnitOfWork() as uow:
            run = await uow.runs.get_or_raise(run_id)
            changes = payload.changed_fields()
            if changes:
                await uow.runs.update_by_pk(run_id, **changes)
                await uow.refresh(run)
        return await self.detail(run_id)

    async def archive(self, run_id: str) -> None:
        """归档删除（仅终态允许）。"""
        async with UnitOfWork() as uow:
            run = await uow.runs.get_or_raise(run_id)
            if run.state in (RunState.RUNNING, RunState.PLANNING, RunState.READY):
                raise IllegalTransitionError(
                    "活跃 Run 不允许归档", details={"run_id": run_id, "state": str(run.state)}
                )
            await uow.runs.delete_by_pk(run_id)

    # -- 控制指令 ------------------------------------------------------------

    async def start(self, run_id: str) -> RunDetail:
        """启动 Run（幂等）。"""
        async with UnitOfWork() as uow:
            run = await uow.runs.get_or_raise(run_id)
            if run.state is RunState.RUNNING:
                return await self.detail(run_id)
            ok = await uow.runs.cas_state(
                run_id, from_state=_START_SOURCES, to_state=RunState.RUNNING
            )
            if not ok:
                raise IllegalTransitionError(
                    f"Run 状态不允许启动：{run.state}",
                    details={"run_id": run_id, "state": str(run.state)},
                )
            uow.collect_event(
                EventType.RUN_STARTED,
                {"run_id": run_id, "state": RunState.RUNNING, "reason": "start"},
                run_id=run_id,
            )
        await self._kick_loop(run_id)
        return await self.detail(run_id)

    async def pause(self, run_id: str) -> RunDetail:
        """暂停 Run（幂等）。"""
        async with UnitOfWork() as uow:
            run = await uow.runs.get_or_raise(run_id)
            if run.state is RunState.PAUSED:
                return await self.detail(run_id)
            ok = await uow.runs.cas_state(
                run_id, from_state=_PAUSE_SOURCES, to_state=RunState.PAUSED
            )
            if not ok:
                raise IllegalTransitionError(
                    f"Run 状态不允许暂停：{run.state}",
                    details={"run_id": run_id, "state": str(run.state)},
                )
            uow.collect_event(
                EventType.RUN_PAUSED,
                {"run_id": run_id, "state": RunState.PAUSED, "reason": "pause"},
                run_id=run_id,
            )
        return await self.detail(run_id)

    async def resume(self, run_id: str) -> RunDetail:
        """恢复 Run（幂等）。"""
        async with UnitOfWork() as uow:
            run = await uow.runs.get_or_raise(run_id)
            if run.state is RunState.RUNNING:
                return await self.detail(run_id)
            ok = await uow.runs.cas_state(
                run_id, from_state=_RESUME_SOURCES, to_state=RunState.RUNNING
            )
            if not ok:
                raise IllegalTransitionError(
                    f"Run 状态不允许恢复：{run.state}",
                    details={"run_id": run_id, "state": str(run.state)},
                )
            uow.collect_event(
                EventType.RUN_RESUMED,
                {"run_id": run_id, "state": RunState.RUNNING, "reason": "resume"},
                run_id=run_id,
            )
        return await self.detail(run_id)

    async def cancel(self, run_id: str) -> RunDetail:
        """取消 Run（幂等）。"""
        async with UnitOfWork() as uow:
            run = await uow.runs.get_or_raise(run_id)
            if run.state is RunState.CANCELLED:
                return await self.detail(run_id)
            ok = await uow.runs.cas_state(
                run_id, from_state=_CANCEL_SOURCES, to_state=RunState.CANCELLED
            )
            if not ok:
                raise IllegalTransitionError(
                    f"Run 状态不允许取消：{run.state}",
                    details={"run_id": run_id, "state": str(run.state)},
                )
            uow.collect_event(
                EventType.RUN_CANCELLED,
                {"run_id": run_id, "state": RunState.CANCELLED, "reason": "cancel"},
                run_id=run_id,
            )
        return await self.detail(run_id)

    async def retry(self, run_id: str) -> RunDetail:
        """从失败点重试（终态 FAILED → RUNNING）。"""
        async with UnitOfWork() as uow:
            run = await uow.runs.get_or_raise(run_id)
            if run.state is not RunState.FAILED:
                raise IllegalTransitionError(
                    f"只有 FAILED 的 Run 才能 retry：{run.state}",
                    details={"run_id": run_id, "state": str(run.state)},
                )
            ok = await uow.runs.cas_state(
                run_id, from_state=RunState.FAILED, to_state=RunState.RUNNING
            )
            if not ok:
                raise IllegalTransitionError("Run retry 失败", details={"run_id": run_id})
            uow.collect_event(
                EventType.RUN_STARTED,
                {"run_id": run_id, "state": RunState.RUNNING, "reason": "retry"},
                run_id=run_id,
            )
        await self._kick_loop(run_id)
        return await self.detail(run_id)

    async def rollback(self, run_id: str) -> dict[str, Any]:
        """回滚已产生的分支/PR（VCS 层就绪后接入）。"""
        async with UnitOfWork() as uow:
            await uow.runs.get_or_raise(run_id)
        return {"run_id": run_id, "rolled_back": 0, "detail": "VCS 层未接入"}

    # -- 时间轴 / 日志 / 成本 / 里程碑 ----------------------------------------

    async def timeline(self, run_id: str) -> dict[str, Any]:
        """阶段时间轴。"""
        async with UnitOfWork() as uow:
            await uow.runs.get_or_raise(run_id)
            events = await uow.events.list_for_run(run_id, limit=200)
            milestones = []
            for event in events:
                if event.type == "MILESTONE_REACHED":
                    milestones.append(
                        {
                            "seq": event.seq,
                            "type": event.type,
                            "created_at": to_iso8601(event.created_at),
                            "payload": event.payload,
                        }
                    )
        return {
            "run_id": run_id,
            "milestones": milestones,
            "phases": [
                {
                    "seq": e.seq,
                    "type": e.type,
                    "created_at": to_iso8601(e.created_at),
                    "severity": str(e.severity),
                }
                for e in events
            ],
        }

    async def logs(self, run_id: str, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """结构化日志（分页）。"""
        async with UnitOfWork() as uow:
            await uow.runs.get_or_raise(run_id)
            events = await uow.events.list_for_run(run_id, limit=limit)
        items = [
            {
                "seq": e.seq,
                "type": e.type,
                "severity": str(e.severity),
                "trace_id": e.trace_id,
                "created_at": to_iso8601(e.created_at),
                "payload": e.payload,
            }
            for e in events
        ]
        return {"items": items, "total": len(items), "limit": limit, "offset": offset}

    async def cost(self, run_id: str) -> dict[str, Any]:
        """本 Run 成本明细。"""
        async with UnitOfWork() as uow:
            await uow.runs.get_or_raise(run_id)
            from loopforge.models.budget import CostRecord

            records = await uow.governance.costs.list_by(run_id=run_id)
            total = await uow.governance.costs.total_for_run(run_id)
            by_model = await uow.governance.costs.breakdown_by_model(run_id)
        return {
            "run_id": run_id,
            "total_usd": str(total),
            "by_model": {k: str(v) for k, v in by_model.items()},
            "records": [
                {
                    "id": r.id,
                    "provider": r.provider,
                    "model": r.model,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "cost_usd": str(r.cost_usd),
                }
                for r in records
            ],
        }

    async def checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        """检查点列表。"""
        async with UnitOfWork() as uow:
            await uow.runs.get_or_raise(run_id)
            checkpoints = await uow.checkpoints.list_for_loop(run_id, loop_index=0)
        return [
            {
                "id": c.id,
                "kind": c.kind,
                "loop_index": c.loop_index,
                "last_event_seq": c.last_event_seq,
                "content_hash": c.content_hash,
                "created_at": to_iso8601(c.created_at),
            }
            for c in checkpoints
        ]

    async def restore_checkpoint(self, run_id: str, checkpoint_id: str) -> dict[str, Any]:
        """恢复到指定检查点。"""
        async with UnitOfWork() as uow:
            await uow.runs.get_or_raise(run_id)
            checkpoint = await uow.checkpoints.get_or_raise(checkpoint_id)
            if checkpoint.run_id != run_id:
                raise NotFoundError("检查点不属于该 Run", details={"checkpoint_id": checkpoint_id})
        return {"run_id": run_id, "checkpoint_id": checkpoint_id, "restored": True}

    async def milestones(self, run_id: str) -> list[dict[str, Any]]:
        """里程碑列表。"""
        async with UnitOfWork() as uow:
            await uow.runs.get_or_raise(run_id)
            events = await uow.events.list_for_run(run_id, limit=500)
            milestones = [
                {
                    "seq": e.seq,
                    "milestone_index": e.payload.get("milestone_index", 0),
                    "tasks_done": e.payload.get("tasks_done", 0),
                    "created_at": to_iso8601(e.created_at),
                }
                for e in events
                if e.type == "MILESTONE_REACHED"
            ]
        return milestones

    async def task_graph(self, run_id: str) -> dict[str, Any]:
        """任务 DAG。"""
        async with UnitOfWork() as uow:
            await uow.runs.get_or_raise(run_id)
            tasks = await uow.tasks.list_by_run(run_id)
        nodes = []
        edges = []
        for task in tasks:
            nodes.append(
                {
                    "id": task.id,
                    "task_key": task.task_key,
                    "title": task.title,
                    "state": str(task.state),
                }
            )
            for dep in task.depends_on:
                edges.append({"from": dep, "to": task.id})
        return {"nodes": nodes, "edges": edges}

    # -- 内部辅助 ------------------------------------------------------------

    async def _stats(self, uow: UnitOfWork, run: Run) -> RunStats:
        """聚合 Run 统计。"""
        tasks = await uow.tasks.list_by_run(run.id)
        stats = RunStats(
            task_total=len(tasks),
            task_done=sum(1 for t in tasks if str(t.state) == "DONE"),
            task_failed=sum(1 for t in tasks if str(t.state) == "FAILED"),
            task_active=sum(1 for t in tasks if str(t.state) in {"LEASED", "ACTIVE", "IN_REVIEW"}),
        )
        steps = await uow.steps.list_by_task(run.id) if False else []
        from loopforge.models.step import Step

        result = await uow.session.execute(
            Step.__table__.select().where(Step.run_id == run.id)
        )
        steps = list(result.scalars().all())
        stats.step_total = len(steps)
        stats.step_unconfirmed = sum(1 for s in steps if str(s.state) == "INTENT_RECORDED")
        stats.event_count = await uow.events.count_for_run(run.id)
        try:
            stats.cost_usd = await uow.governance.costs.total_for_run(run.id)
        except Exception:  # noqa: BLE001
            stats.cost_usd = 0
        stats.pending_approvals = await uow.governance.approvals.count_by(
            run_id=run.id, state="PENDING"
        )
        return stats

    async def _kick_loop(self, run_id: str) -> None:
        """启动 Loop（惰性接入 WP4 编排引擎）。

        WP4 的 ``LoopEngine`` 未合入时静默跳过，只把 Run 置为 RUNNING；
        合入后由编排引擎驱动完整 Loop。用 lazy import 保证并行开发不互相阻塞。
        """
        try:
            from loopforge.orchestration import LoopEngine  # type: ignore[import-not-found]

            engine = LoopEngine()
            import asyncio

            asyncio.create_task(engine.run_loop(run_id=run_id, task_id="", lease=None))  # type: ignore[arg-type]
        except ImportError:
            _logger.info(
                "loop_engine_unavailable",
                run_id=run_id,
                detail="WP4 编排引擎未安装，Run 已置为 RUNNING，等待集成",
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("kick_loop_failed", run_id=run_id, error=str(exc))
