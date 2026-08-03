"""租约管理器 —— 乐观锁 CAS 领取 / 心跳 / 续期 / 释放 / 吊销 / 过期回收。

来源：``docs/ARCHITECTURE.md`` §8.3、``docs/TASK_BREAKDOWN.md`` §6.3.3 / §11.6。

五条铁律的落地
--------------
* L1：领取走 :meth:`LeaseRepository.acquire_lease_cas` 的条件 UPDATE + version
  乐观锁；不引入 Redis 锁、不用 ``SELECT ... FOR UPDATE``。
* L2：每次续租/心跳返回 ``fence_token``；写操作前调用
  :meth:`LeaseRepository.verify_fence` 校验，旧 token 的写入被拒绝。
* L3：租约超时 ≠ 任务失败 —— 回收只加 ``reclaim_count``，绝不加 ``attempt_count``。
* L4：回收前必须过 ``grace_s``，并要求「租约过期」+「Worker 心跳失联」双证据。
* L5：``uq_leases_active_task`` partial unique index 是 DB 层第二道防线。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.clock import get_clock
from loopforge.core.config import LeaseSettings, get_settings
from loopforge.core.enums import LeaseState, RunState, TaskState
from loopforge.core.errors import FencedOutError, LeaseLostError
from loopforge.core.event_types import EventType
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork
from loopforge.models.lease import Lease
from loopforge.models.task import Task

__all__ = [
    "LeaseHeartbeat",
    "ReclaimResult",
    "LeaseManager",
]


class LeaseHeartbeat(BaseModel):
    """心跳返回（冻结契约 §11.6 的 ``LeaseHeartbeat``）。"""

    model_config = ConfigDict(extra="forbid")

    lease_id: str
    fence_token: int
    expires_at: Any = None
    heartbeat_count: int = 0
    extend_count: int = 0
    ok: bool = True


class ReclaimResult(BaseModel):
    """一次租约回收的结果。"""

    model_config = ConfigDict(extra="forbid")

    lease_id: str
    task_id: str
    worker_id: str
    fence_token: int
    reason: str
    reclaim_count: int = 0
    task_state: TaskState = TaskState.READY
    task_failed: bool = False


class LeaseManager:
    """租约全生命周期管理（冻结契约 §11.6）。

    :param session_factory: session 工厂；``None`` 使用全局。
    :param settings: 租约配置；``None`` 使用全局配置。
    """

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        settings: LeaseSettings | None = None,
    ) -> None:
        self._session_factory: Any = (
            session_factory if session_factory is not None else get_session_factory()
        )
        self._settings: LeaseSettings = settings if settings is not None else get_settings().lease

    # -- 领取 ---------------------------------------------------------------

    async def acquire(
        self, *, task_id: str, worker_id: str, expected_version: int, ttl_s: int
    ) -> Lease | None:
        """CAS 领取任务并签发租约。

        :param task_id: 目标 Task。
        :param worker_id: 领取者。
        :param expected_version: 候选查询得到的版本号。
        :param ttl_s: 租约 TTL 秒数。
        :returns: 签发成功的租约；被别人抢先返回 ``None``（常态，不是异常）。
        """
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            lease = await uow.leases.acquire_lease_cas(
                task_id,
                worker_id,
                expected_version=expected_version,
                ttl_seconds=ttl_s,
                max_ttl_seconds=self._settings.max_ttl_s,
            )
            if lease is None:
                await uow.rollback()
                return None
            await uow.system.workers.heartbeat(
                worker_id, state="busy", current_task_id=task_id
            )
            uow.collect_event(
                EventType.LEASE_ACQUIRED,
                {
                    "lease_id": lease.id,
                    "task_id": lease.task_id,
                    "worker_id": lease.worker_id,
                    "fence_token": lease.fence_token,
                    "state": LeaseState.ACTIVE,
                    "ttl_seconds": lease.ttl_seconds,
                    "heartbeat_count": 0,
                },
                run_id=None,
                task_id=task_id,
            )
            uow.collect_event(
                EventType.TASK_LEASED,
                {
                    "run_id": "",
                    "task_id": task_id,
                    "task_key": "",
                    "state": TaskState.LEASED,
                    "previous_state": TaskState.READY,
                    "reason": f"worker={worker_id}",
                },
                run_id=None,
                task_id=task_id,
            )
            await uow.commit()
            return lease

    # -- 心跳 / 续期 ---------------------------------------------------------

    async def heartbeat(
        self,
        *,
        lease_id: str,
        fence_token: int,
        progress_hint: str | None = None,
    ) -> LeaseHeartbeat:
        """刷新心跳（可顺带续期）。

        :param lease_id: 租约主键。
        :param fence_token: 持有者持有的 fence token。
        :param progress_hint: 进度提示（写入租约行，Watchdog 参考）。
        :returns: 刷新后的心跳信息。
        :raises LeaseLostError: 租约不存在 / 非 ACTIVE / fence 不匹配 /
            已被 Watchdog 回收（``LF-LEASE-0502``）。
        """
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            lease = await uow.leases.get(lease_id)
            if lease is None or lease.state is not LeaseState.ACTIVE:
                raise LeaseLostError(
                    "租约不存在或已失效",
                    details={"lease_id": lease_id, "presented_token": fence_token},
                )
            if lease.fence_token != fence_token:
                raise LeaseLostError(
                    "fence token 不匹配，租约可能已被重新签发",
                    details={
                        "lease_id": lease_id,
                        "presented": fence_token,
                        "holder": lease.fence_token,
                    },
                )
            # 双保险：Task 侧 fence 校验（Watchdog 回收会 bump task fence）。
            await uow.leases.verify_fence(lease.task_id, fence_token)

            try:
                refreshed = await uow.leases.heartbeat(
                    lease_id, worker_id=lease.worker_id, extend=True
                )
            except LeaseLostError:
                raise
            uow.collect_event(
                EventType.LEASE_HEARTBEAT,
                {
                    "lease_id": lease_id,
                    "task_id": lease.task_id,
                    "worker_id": lease.worker_id,
                    "fence_token": fence_token,
                    "state": LeaseState.ACTIVE,
                    "ttl_seconds": refreshed.ttl_seconds,
                    "heartbeat_count": refreshed.heartbeat_count,
                    "reason": progress_hint or "",
                },
                task_id=lease.task_id,
            )
            await uow.commit()
            return LeaseHeartbeat(
                lease_id=lease_id,
                fence_token=fence_token,
                expires_at=refreshed.expires_at,
                heartbeat_count=refreshed.heartbeat_count,
                extend_count=refreshed.extend_count,
            )

    async def extend(
        self, *, lease_id: str, fence_token: int, extra_s: int, reason: str
    ) -> Lease:
        """显式续期（已知长耗时操作前调用）。

        :param lease_id: 租约主键。
        :param fence_token: 持有者 fence token。
        :param extra_s: 追加的存活秒数。
        :param reason: 续期原因。
        :returns: 续期后的租约。
        :raises LeaseLostError: 租约丢失 / fence 不匹配。
        :raises FencedOutError: fence token 已过期。
        """
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            lease = await uow.leases.get(lease_id)
            if lease is None or lease.state is not LeaseState.ACTIVE:
                raise LeaseLostError(
                    "租约不存在或已失效",
                    details={"lease_id": lease_id, "presented_token": fence_token},
                )
            if lease.fence_token != fence_token:
                raise FencedOutError(
                    "fence token 已过期，续期被拒绝",
                    details={
                        "lease_id": lease_id,
                        "presented": fence_token,
                        "holder": lease.fence_token,
                    },
                )
            if lease.extend_count >= self._settings.max_extends:
                raise LeaseLostError(
                    "续期次数已达上限，疑似卡死",
                    details={
                        "lease_id": lease_id,
                        "extend_count": lease.extend_count,
                        "max_extends": self._settings.max_extends,
                    },
                )
            now = get_clock().now()
            new_expiry = now + timedelta(seconds=extra_s)
            hard_limit = lease.acquired_at + timedelta(seconds=self._settings.max_ttl_s)
            if new_expiry > hard_limit:
                new_expiry = hard_limit
            result = await uow.session.execute(
                __import__("sqlalchemy").update(Lease)
                .where(Lease.id == lease_id, Lease.state == LeaseState.ACTIVE)
                .values(
                    expires_at=new_expiry,
                    extend_count=Lease.extend_count + 1,
                    ttl_seconds=max(lease.ttl_seconds, extra_s),
                    last_heartbeat_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                raise LeaseLostError(
                    "续期写入未命中活跃租约",
                    details={"lease_id": lease_id},
                )
            await uow.session.refresh(lease)
            uow.collect_event(
                EventType.LEASE_EXTENDED,
                {
                    "lease_id": lease_id,
                    "task_id": lease.task_id,
                    "worker_id": lease.worker_id,
                    "fence_token": fence_token,
                    "state": LeaseState.ACTIVE,
                    "ttl_seconds": lease.ttl_seconds,
                    "extend_count": lease.extend_count,
                    "reason": reason,
                },
                task_id=lease.task_id,
            )
            await uow.commit()
            return lease

    # -- 释放 / 吊销 ---------------------------------------------------------

    async def release(self, *, lease_id: str, fence_token: int, reason: str) -> None:
        """主动释放租约（任务完成 / 主动放弃）。

        :param lease_id: 租约主键。
        :param fence_token: 持有者 fence token。
        :param reason: 释放原因。
        :raises LeaseLostError: 租约丢失 / fence 不匹配。
        """
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            lease = await uow.leases.get(lease_id)
            if lease is None or lease.state is not LeaseState.ACTIVE:
                return
            if lease.fence_token != fence_token:
                raise FencedOutError(
                    "fence token 已过期，释放被拒绝",
                    details={"lease_id": lease_id, "presented": fence_token},
                )
            released = await uow.leases.release(lease_id, reason=reason)
            if released:
                # Task 状态迁移：LEASED -> READY 或 ACTIVE -> READY（主动放弃）。
                task = await uow.tasks.get(lease.task_id)
                if task is not None and task.state in (TaskState.LEASED, TaskState.ACTIVE):
                    await uow.tasks.cas_transition(
                        lease.task_id,
                        from_state=task.state,
                        to_state=TaskState.READY,
                        expected_version=task.version,
                        extra={"current_lease_id": None},
                    )
                uow.collect_event(
                    EventType.LEASE_RELEASED,
                    {
                        "lease_id": lease_id,
                        "task_id": lease.task_id,
                        "worker_id": lease.worker_id,
                        "fence_token": fence_token,
                        "state": LeaseState.RELEASED,
                        "reason": reason,
                    },
                    task_id=lease.task_id,
                )
            await uow.commit()

    async def revoke(self, *, lease_id: str, reason: str) -> None:
        """人工吊销租约（管理员操作，不校验 fence）。

        :param lease_id: 租约主键。
        :param reason: 吊销原因。
        """
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            lease = await uow.leases.get(lease_id)
            revoked = await uow.leases.revoke(
                lease_id, reason=reason or "revoked_by_operator"
            )
            if revoked and lease is not None:
                uow.collect_event(
                    EventType.LEASE_REVOKED,
                    {
                        "lease_id": lease_id,
                        "task_id": lease.task_id,
                        "worker_id": lease.worker_id,
                        "fence_token": lease.fence_token,
                        "state": LeaseState.REVOKED,
                        "reason": reason,
                    },
                    task_id=lease.task_id,
                )
            await uow.commit()

    # -- 过期回收 ------------------------------------------------------------

    async def reclaim_expired(
        self, *, grace_s: int, limit: int = 100
    ) -> list[ReclaimResult]:
        """回收过期租约（Watchdog 调用）。

        铁律 L3/L4：只回收「过期 + Worker 心跳失联」双证据的租约，
        只加 ``reclaim_count`` 不碰 ``attempt_count``；超上限才判 Task 失败。

        :param grace_s: 宽限期（租约过期后还要再等这么久）。
        :param limit: 单轮最多处理条数。
        :returns: 回收结果列表。
        """
        now = get_clock().now()
        results: list[ReclaimResult] = []
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            # 1. 捞「已过期 + 超过宽限」的活跃租约。
            cutoff = now - timedelta(seconds=grace_s)
            expired = await uow.leases.list_expired(now=cutoff, limit=limit)
            for lease in expired:
                worker = await uow.system.workers.get(lease.worker_id)
                worker_stale = worker is None or worker.is_stale(now=now, threshold_s=grace_s)
                if not worker_stale:
                    # 慢 Worker 仍在心跳 —— 不误杀（铁律 L4）。
                    continue
                task = await uow.tasks.get(lease.task_id)
                if task is None:
                    continue
                # 2. 租约置 EXPIRED。
                await uow.leases.expire(lease.id, reason="heartbeat_timeout", now=now)
                # 3. Task 回到 READY（LEASED|ACTIVE -> READY），reclaim_count+1，
                #    fence_token+1（旧 Worker 的写入被挡）。
                new_reclaim = task.reclaim_count + 1
                failed = new_reclaim > task.max_reclaims
                if failed:
                    outcome = await uow.tasks.cas_transition(
                        task.id,
                        from_state=task.state,
                        to_state=TaskState.FAILED,
                        expected_version=task.version,
                        bump_fence=True,
                        extra={
                            "current_lease_id": None,
                            "reclaim_count": new_reclaim,
                            "error_code": "LF-LEASE-0009",
                        },
                    )
                    target_state = TaskState.FAILED
                else:
                    outcome = await uow.tasks.cas_transition(
                        task.id,
                        from_state=task.state,
                        to_state=TaskState.READY,
                        expected_version=task.version,
                        bump_fence=True,
                        extra={
                            "current_lease_id": None,
                            "reclaim_count": new_reclaim,
                        },
                    )
                    target_state = TaskState.READY
                if not outcome.succeeded:
                    continue
                uow.collect_event(
                    EventType.LEASE_RECLAIMED,
                    {
                        "lease_id": lease.id,
                        "task_id": lease.task_id,
                        "worker_id": lease.worker_id,
                        "fence_token": lease.fence_token,
                        "expiry_reason": "heartbeat_timeout",
                        "reclaim_count": new_reclaim,
                        "grace_seconds": grace_s,
                    },
                    task_id=lease.task_id,
                )
                results.append(
                    ReclaimResult(
                        lease_id=lease.id,
                        task_id=lease.task_id,
                        worker_id=lease.worker_id,
                        fence_token=lease.fence_token,
                        reason="heartbeat_timeout",
                        reclaim_count=new_reclaim,
                        task_state=target_state,
                        task_failed=failed,
                    )
                )
            await uow.commit()
        return results
