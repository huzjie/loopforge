"""Worker 与租约 DTO —— 执行面的注册、心跳、租约与优雅下线。

租约（Lease）是「零重复获取」的核心：同一个 Task 在任意时刻只能有一个
``ACTIVE`` 租约，由 DB 的部分唯一索引强制。本模块的 DTO 只负责把这套
契约暴露给 API 层，任何续租/抢占都必须带上 ``fence_token`` 做 CAS。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from loopforge.core.enums import LeaseState
from loopforge.schemas.common import LFModel, ORMModel

__all__ = [
    "WORKER_STATES",
    "WorkerRegister",
    "WorkerHeartbeat",
    "WorkerRead",
    "WorkerQuery",
    "WorkerDrainRequest",
    "LeaseAcquireRequest",
    "LeaseRenewRequest",
    "LeaseReleaseRequest",
    "LeaseRead",
    "LeaseQuery",
    "ReconcileReport",
]

#: Worker 状态取值。
WORKER_STATES: tuple[str, ...] = ("idle", "busy", "draining", "dead")


class WorkerRegister(LFModel):
    """Worker 启动时注册自己。"""

    id: str | None = Field(default=None, max_length=26, description="Worker ID，省略则服务端生成")
    hostname: str = Field(default="", max_length=255, description="主机名")
    pid: int = Field(default=0, ge=0, description="进程号")
    version: str = Field(default="", max_length=32, description="Worker 版本")
    capabilities: dict[str, object] = Field(default_factory=dict, description="能力声明")

    @field_validator("hostname")
    @classmethod
    def _strip_hostname(cls, value: str) -> str:
        """去掉空白。"""
        return value.strip()


class WorkerHeartbeat(LFModel):
    """心跳上报。"""

    worker_id: str = Field(min_length=1, max_length=26, description="Worker ID")
    state: str = Field(default="idle", max_length=32, description="当前状态")
    current_task_id: str | None = Field(default=None, max_length=26, description="正在处理的 Task")
    load: float = Field(default=0.0, ge=0.0, le=1.0, description="负载比例")

    @field_validator("state")
    @classmethod
    def _check_state(cls, value: str) -> str:
        """状态必须在白名单内。"""
        normalized = value.strip().lower()
        if normalized not in WORKER_STATES:
            raise ValueError(f"state 必须是 {WORKER_STATES} 之一，收到 {value!r}")
        return normalized

    @model_validator(mode="after")
    def _check_busy_has_task(self) -> WorkerHeartbeat:
        """声称 busy 却没有任务，说明状态机已经漂了，必须拦下来。"""
        if self.state == "busy" and not self.current_task_id:
            raise ValueError("state=busy 时必须提供 current_task_id")
        return self


class WorkerRead(ORMModel):
    """Worker 读模型。"""

    id: str = Field(description="Worker ID")
    hostname: str = Field(default="", description="主机名")
    pid: int = Field(default=0, description="进程号")
    version: str = Field(default="", description="版本")
    capabilities: dict[str, object] = Field(default_factory=dict, description="能力声明")
    state: str = Field(default="idle", description="状态")
    current_task_id: str | None = Field(default=None, description="当前任务")
    drain: bool = Field(default=False, description="是否处于排空")
    started_at: datetime = Field(description="启动时间")
    last_heartbeat_at: datetime = Field(description="最后心跳时间")

    def is_stale(self, now: datetime, ttl_seconds: int) -> bool:
        """按 TTL 判断心跳是否已过期。"""
        return (now - self.last_heartbeat_at).total_seconds() > ttl_seconds

    @property
    def is_available(self) -> bool:
        """是否可以接新任务。"""
        return self.state == "idle" and not self.drain


class WorkerQuery(LFModel):
    """Worker 查询条件。"""

    state: str | None = Field(default=None, max_length=32, description="按状态过滤")
    alive_only: bool = Field(default=True, description="只看心跳存活的")
    hostname: str | None = Field(default=None, max_length=255, description="按主机名过滤")


class WorkerDrainRequest(LFModel):
    """请求某个 Worker 优雅下线。"""

    worker_id: str = Field(min_length=1, max_length=26, description="Worker ID")
    reason: str = Field(default="", max_length=1_000, description="下线原因")
    force: bool = Field(default=False, description="是否直接标记 dead 而不等待任务收尾")


class LeaseAcquireRequest(LFModel):
    """申请一个任务租约。"""

    task_id: str = Field(min_length=1, max_length=26, description="目标 Task")
    worker_id: str = Field(min_length=1, max_length=26, description="申请方 Worker")
    ttl_seconds: int = Field(default=300, ge=5, le=24 * 3600, description="租约时长")

    @property
    def ttl_is_short(self) -> bool:
        """TTL 是否短于常见的单步耗时（用于服务层告警）。"""
        return self.ttl_seconds < 60


class LeaseRenewRequest(LFModel):
    """续租 —— 必须带 fence_token 做 CAS，防止过期后的僵尸 Worker 写回。"""

    lease_id: str = Field(min_length=1, max_length=26, description="租约 ID")
    worker_id: str = Field(min_length=1, max_length=26, description="持有方 Worker")
    fence_token: int = Field(ge=0, description="栅栏令牌")
    ttl_seconds: int = Field(default=300, ge=5, le=24 * 3600, description="新的租约时长")


class LeaseReleaseRequest(LFModel):
    """释放租约。"""

    lease_id: str = Field(min_length=1, max_length=26, description="租约 ID")
    worker_id: str = Field(min_length=1, max_length=26, description="持有方 Worker")
    fence_token: int = Field(ge=0, description="栅栏令牌")
    outcome: str = Field(default="done", max_length=32, description="收尾结论：done/failed/abort")

    @field_validator("outcome")
    @classmethod
    def _check_outcome(cls, value: str) -> str:
        """结论必须在白名单内。"""
        allowed = ("done", "failed", "abort")
        normalized = value.strip().lower()
        if normalized not in allowed:
            raise ValueError(f"outcome 必须是 {allowed} 之一，收到 {value!r}")
        return normalized


class LeaseRead(ORMModel):
    """租约读模型。"""

    id: str = Field(description="租约 ID")
    task_id: str = Field(description="目标 Task")
    worker_id: str = Field(description="持有方 Worker")
    state: LeaseState = Field(description="租约状态")
    fence_token: int = Field(default=0, description="栅栏令牌")
    acquired_at: datetime = Field(description="获取时间")
    expires_at: datetime = Field(description="过期时间")
    released_at: datetime | None = Field(default=None, description="释放时间")
    renew_count: int = Field(default=0, ge=0, description="续租次数")

    @property
    def is_active(self) -> bool:
        """是否仍在生效状态。"""
        return self.state is LeaseState.ACTIVE

    def is_expired(self, now: datetime) -> bool:
        """按当前时间判断是否已过期。"""
        return now >= self.expires_at

    def seconds_left(self, now: datetime) -> int:
        """剩余秒数，已过期返回 0。"""
        delta = (self.expires_at - now).total_seconds()
        return int(delta) if delta > 0 else 0


class LeaseQuery(LFModel):
    """租约查询条件。"""

    task_id: str | None = Field(default=None, max_length=26, description="按 Task 过滤")
    worker_id: str | None = Field(default=None, max_length=26, description="按 Worker 过滤")
    state: LeaseState | None = Field(default=None, description="按状态过滤")
    expired_only: bool = Field(default=False, description="只看已过期的")


class ReconcileReport(LFModel):
    """一次对账（reconcile）的结果。"""

    expired_leases: int = Field(default=0, ge=0, description="回收的过期租约数")
    dead_workers: int = Field(default=0, ge=0, description="标记为 dead 的 Worker 数")
    requeued_tasks: int = Field(default=0, ge=0, description="重新入队的任务数")
    orphan_steps: int = Field(default=0, ge=0, description="发现的孤儿 Step 数")
    started_at: datetime | None = Field(default=None, description="对账开始时间")
    finished_at: datetime | None = Field(default=None, description="对账结束时间")
    notes: list[str] = Field(default_factory=list, description="补充说明")

    @property
    def had_changes(self) -> bool:
        """本轮对账是否实际动过数据。"""
        return bool(
            self.expired_leases or self.dead_workers or self.requeued_tasks or self.orphan_steps
        )

    @property
    def duration_seconds(self) -> float:
        """耗时秒数；时间缺失时返回 0.0。"""
        if self.started_at is None or self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()
