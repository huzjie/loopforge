"""Lease 仓储 —— **零重复领取**的实现处。

来源：``docs/ARCHITECTURE.md`` §7、``docs/TASK_BREAKDOWN.md`` §3.1.3。

:meth:`LeaseRepository.acquire_lease_cas` 的三步走：

1. **CAS 抢占 Task**（``READY → LEASED``，同时 ``version+1``、``fence_token+1``）——
   失败即返回 ``None``，让 Worker 去抢下一个；
2. **插入 ACTIVE 租约**，携带刚刚递增后的 ``fence_token``；
   若 partial unique index ``uq_leases_active_task`` 报冲突，说明第 1 步的
   CAS 之外还有漏网之鱼，**回滚到 savepoint 并返回 None**（DB 层第二道防线生效）；
3. **回写 ``tasks.current_lease_id``**。

全程无 ``SELECT … FOR UPDATE``，因此 SQLite 也能跑；正确性由「条件 UPDATE 的
rowcount」和「唯一索引」共同保证，不依赖任何数据库特有的锁语义。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError

from loopforge.constants import DEFAULT_LEASE_TTL_S, MAX_LEASE_TTL_S
from loopforge.core.clock import get_clock
from loopforge.core.enums import LeaseState, TaskState
from loopforge.core.errors import FencedOutError, LeaseLostError
from loopforge.core.ids import new_ulid
from loopforge.db.repositories.base import BaseRepository, clamp_limit
from loopforge.db.repositories.task_repo import TaskRepository
from loopforge.models.lease import Lease
from loopforge.models.task import Task

__all__ = ["LeaseRepository"]


class LeaseRepository(BaseRepository[Lease]):
    """租约的签发、心跳、续期、释放与过期回收。"""

    model = Lease

    # -- 领取 ---------------------------------------------------------------

    async def acquire_lease_cas(
        self,
        task_id: str,
        worker_id: str,
        *,
        expected_version: int,
        ttl_seconds: int = DEFAULT_LEASE_TTL_S,
        max_ttl_seconds: int = MAX_LEASE_TTL_S,
        from_state: TaskState = TaskState.READY,
        to_state: TaskState = TaskState.LEASED,
        now: datetime | None = None,
    ) -> Lease | None:
        """原子领取任务并签发租约。

        :param task_id: 目标 Task。
        :param worker_id: 领取者。
        :param expected_version: CAS 比较用的 Task 版本号（来自候选查询结果）。
        :param ttl_seconds: 租约存活秒数。
        :param max_ttl_seconds: 续期的硬上限。
        :param from_state: 期望的 Task 当前状态。
        :param to_state: 领取后的 Task 状态。
        :param now: 参照时间；``None`` 表示取全局 Clock。
        :returns: 签发成功的 :class:`~loopforge.models.lease.Lease`；
                  被别人抢先则返回 ``None``（**不是异常**，抢不到是常态）。
        """
        moment = now if now is not None else get_clock().now()
        tasks = TaskRepository(self.session)

        # 步骤 1：CAS 抢占 Task，同时把 fence_token 推进一格。
        outcome = await tasks.cas_transition(
            task_id,
            from_state=from_state,
            to_state=to_state,
            expected_version=expected_version,
            bump_fence=True,
            now=moment,
        )
        if not outcome.succeeded:
            return None

        fence_row = await self.session.execute(
            select(Task.fence_token).where(Task.id == task_id)
        )
        fence_token = int(fence_row.scalar_one())

        # 步骤 2：插入 ACTIVE 租约。用 savepoint 包住，冲突时只回滚这一小段。
        lease = Lease(
            id=new_ulid(),
            task_id=task_id,
            worker_id=worker_id,
            fence_token=fence_token,
            state=LeaseState.ACTIVE,
            ttl_seconds=ttl_seconds,
            max_ttl_seconds=max(ttl_seconds, max_ttl_seconds),
            acquired_at=moment,
            expires_at=moment + timedelta(seconds=ttl_seconds),
            last_heartbeat_at=moment,
            heartbeat_count=0,
            extend_count=0,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(lease)
                await self.session.flush()
        except IntegrityError:
            # DB 层第二道防线命中：该 Task 已有活跃租约。
            # 把 Task 状态退回去，让它能被重新领取。
            await tasks.cas_transition(
                task_id,
                from_state=to_state,
                to_state=from_state,
                expected_version=outcome.new_version or expected_version + 1,
                now=moment,
            )
            return None

        # 步骤 3：回写 current_lease_id。
        await self.session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(current_lease_id=lease.id, updated_at=moment)
        )
        return lease

    # -- 心跳与续期 ---------------------------------------------------------

    async def heartbeat(
        self,
        lease_id: str,
        *,
        worker_id: str,
        extend: bool = True,
        now: datetime | None = None,
    ) -> Lease:
        """刷新心跳，可选顺带续期。

        :param lease_id: 租约主键。
        :param worker_id: 心跳发起者，必须与租约持有者一致。
        :param extend: 是否顺带把 ``expires_at`` 往后推。
        :param now: 参照时间。
        :returns: 刷新后的租约对象。
        :raises LeaseLostError: 租约不存在、已非 ACTIVE，或持有者不匹配。
        """
        moment = now if now is not None else get_clock().now()
        lease = await self.get(lease_id)
        if lease is None or lease.state is not LeaseState.ACTIVE:
            raise LeaseLostError(
                "租约不存在或已失效", details={"lease_id": lease_id, "worker_id": worker_id}
            )
        if lease.worker_id != worker_id:
            raise LeaseLostError(
                "租约持有者不匹配",
                details={
                    "lease_id": lease_id,
                    "holder": lease.worker_id,
                    "caller": worker_id,
                },
            )

        values: dict[str, object] = {
            "last_heartbeat_at": moment,
            "heartbeat_count": Lease.heartbeat_count + 1,
        }
        if extend:
            values["expires_at"] = lease.next_expiry(now=moment)
            values["extend_count"] = Lease.extend_count + 1

        result = await self.session.execute(
            update(Lease)
            .where(Lease.id == lease_id, Lease.state == LeaseState.ACTIVE)
            .values(**values)
        )
        if int(result.rowcount or 0) != 1:
            raise LeaseLostError(
                "心跳写入未命中活跃租约（可能刚被 Watchdog 回收）",
                details={"lease_id": lease_id},
            )
        await self.session.refresh(lease)
        return lease

    async def verify_fence(self, task_id: str, fence_token: int) -> None:
        """校验 fence token 是否仍然有效。

        任何带副作用的写操作前都应调用一次；旧租约持有者会在此被挡下。

        :raises FencedOutError: token 落后于 Task 当前值。
        """
        row = await self.session.execute(select(Task.fence_token).where(Task.id == task_id))
        current = row.scalar_one_or_none()
        if current is None or int(current) != fence_token:
            raise FencedOutError(
                "fence token 已过期，写操作被拒绝",
                details={
                    "task_id": task_id,
                    "presented": fence_token,
                    "current": None if current is None else int(current),
                },
            )

    # -- 释放与回收 ---------------------------------------------------------

    async def release(
        self,
        lease_id: str,
        *,
        reason: str = "completed",
        state: LeaseState = LeaseState.RELEASED,
        now: datetime | None = None,
    ) -> bool:
        """释放租约并清空 Task 的 ``current_lease_id``。

        :returns: 是否确实释放了一条活跃租约。
        """
        moment = now if now is not None else get_clock().now()
        result = await self.session.execute(
            update(Lease)
            .where(Lease.id == lease_id, Lease.state == LeaseState.ACTIVE)
            .values(state=state, released_at=moment, expiry_reason=reason)
        )
        if int(result.rowcount or 0) != 1:
            return False
        await self.session.execute(
            update(Task)
            .where(Task.current_lease_id == lease_id)
            .values(current_lease_id=None, updated_at=moment)
        )
        return True

    async def list_expired(
        self, *, now: datetime | None = None, limit: int | None = None
    ) -> list[Lease]:
        """列出已过期但仍标记 ACTIVE 的租约（Watchdog 每轮扫描）。

        命中 ``ix_leases_expiry(state, expires_at)``。
        """
        moment = now if now is not None else get_clock().now()
        stmt = (
            select(Lease)
            .where(and_(Lease.state == LeaseState.ACTIVE, Lease.expires_at <= moment))
            .order_by(Lease.expires_at)
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def expire(
        self, lease_id: str, *, reason: str = "ttl_exceeded", now: datetime | None = None
    ) -> bool:
        """把一条过期租约标记为 EXPIRED。"""
        return await self.release(
            lease_id, reason=reason, state=LeaseState.EXPIRED, now=now
        )

    async def revoke(
        self, lease_id: str, *, reason: str = "revoked", now: datetime | None = None
    ) -> bool:
        """人工吊销一条租约。"""
        return await self.release(
            lease_id, reason=reason, state=LeaseState.REVOKED, now=now
        )

    # -- 查询 ---------------------------------------------------------------

    async def get_active_for_task(self, task_id: str) -> Lease | None:
        """取某 Task 当前的活跃租约（至多一条，由唯一索引保证）。"""
        result = await self.session.execute(
            select(Lease).where(Lease.task_id == task_id, Lease.state == LeaseState.ACTIVE)
        )
        return result.scalar_one_or_none()

    async def list_by_worker(
        self, worker_id: str, *, active_only: bool = True, limit: int | None = None
    ) -> list[Lease]:
        """列出某 Worker 持有的租约。"""
        stmt = select(Lease).where(Lease.worker_id == worker_id)
        if active_only:
            stmt = stmt.where(Lease.state == LeaseState.ACTIVE)
        stmt = stmt.order_by(Lease.acquired_at.desc()).limit(clamp_limit(limit))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_active(self) -> int:
        """当前活跃租约总数（等价于「正在被执行的任务数」）。"""
        result = await self.session.execute(
            select(func.count()).select_from(Lease).where(Lease.state == LeaseState.ACTIVE)
        )
        return int(result.scalar_one())
