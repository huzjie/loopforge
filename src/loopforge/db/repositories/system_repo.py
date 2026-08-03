"""系统域仓储：Worker / ProviderConfig / AgentRole / User / ApiKey / 自进化 / Webhook。

来源：``docs/TASK_BREAKDOWN.md`` §3.1.3、``docs/ARCHITECTURE.md`` §5.1。

这些表的共同点是「运维态」而非「运行态」：写入频率低、读放大高、几乎全部
带 ``enabled`` / ``active`` / ``revoked`` 这类开关列。因此仓储方法以
**幂等 upsert + 开关切换**为主，不涉及 CAS。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update

from loopforge.constants import DEFAULT_HEARTBEAT_INTERVAL_S
from loopforge.core.clock import get_clock
from loopforge.core.enums import RoleName
from loopforge.core.errors import NotFoundError, ValidationError
from loopforge.core.hashing import simhash64
from loopforge.db.mixins import utcnow
from loopforge.db.repositories.base import BaseRepository, clamp_limit
from loopforge.models.agent_role import AgentRole, ProviderConfig
from loopforge.models.auth import USER_ROLES, ApiKey, User
from loopforge.models.evolution import EVOLUTION_STATES, EvolutionItem, WebhookDelivery
from loopforge.models.worker import WORKER_STATES, Worker

__all__ = [
    "WorkerRepository",
    "ProviderConfigRepository",
    "AgentRoleRepository",
    "UserRepository",
    "ApiKeyRepository",
    "EvolutionItemRepository",
    "WebhookDeliveryRepository",
]


class WorkerRepository(BaseRepository[Worker]):
    """``workers`` 表仓储 —— 注册、心跳、失联判定与优雅下线。"""

    model = Worker

    async def register(
        self,
        *,
        worker_id: str | None = None,
        hostname: str = "",
        pid: int = 0,
        version: str = "",
        capabilities: dict[str, Any] | None = None,
    ) -> Worker:
        """注册（或重新注册）一个 Worker。

        Worker 进程重启后会带着同一个 ``worker_id`` 回来，此时不应该新建行，
        而是把状态刷回 ``idle`` 并更新心跳 —— 否则 ``workers`` 表会随重启次数
        无限膨胀，Watchdog 的扫描成本随之上升。

        :param worker_id: 期望的 Worker ID；``None`` 表示由数据库侧生成 ULID。
        :param hostname: 主机名。
        :param pid: 进程号。
        :param version: LoopForge 版本号。
        :param capabilities: 能力声明（语言、执行器、工具集）。
        :returns: 已 flush 的 Worker 行。
        """
        now = utcnow()
        caps = dict(capabilities) if capabilities else {}
        if worker_id is not None:
            existing = await self.get(worker_id)
            if existing is not None:
                existing.hostname = hostname or existing.hostname
                existing.pid = pid
                existing.version = version or existing.version
                existing.capabilities = caps or existing.capabilities
                existing.state = "idle"
                existing.current_task_id = None
                existing.drain = False
                existing.started_at = now
                existing.last_heartbeat_at = now
                await self.session.flush()
                return existing
        values: dict[str, Any] = {
            "hostname": hostname,
            "pid": pid,
            "version": version,
            "capabilities": caps,
            "state": "idle",
            "drain": False,
            "started_at": now,
            "last_heartbeat_at": now,
        }
        if worker_id is not None:
            values["id"] = worker_id
        return await self.create(**values)

    async def heartbeat(
        self,
        worker_id: str,
        *,
        state: str | None = None,
        current_task_id: str | None = None,
    ) -> bool:
        """刷新心跳（可同时更新状态与当前任务）。

        :param worker_id: Worker ID。
        :param state: 新状态；``None`` 表示保持不变。
        :param current_task_id: 当前处理的 Task ID；显式传 ``""`` 表示清空。
        :returns: 是否命中一行。
        :raises ValidationError: ``state`` 不在 :data:`~loopforge.models.worker.WORKER_STATES`。
        """
        values: dict[str, Any] = {"last_heartbeat_at": utcnow()}
        if state is not None:
            if state not in WORKER_STATES:
                raise ValidationError(
                    f"非法 Worker 状态 {state!r}",
                    details={"allowed": list(WORKER_STATES)},
                )
            values["state"] = state
        if current_task_id is not None:
            values["current_task_id"] = current_task_id or None
        stmt = update(Worker).where(Worker.id == worker_id).values(**values)
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0) > 0

    async def set_state(self, worker_id: str, state: str) -> bool:
        """切换 Worker 状态。

        :raises ValidationError: 状态非法。
        """
        if state not in WORKER_STATES:
            raise ValidationError(
                f"非法 Worker 状态 {state!r}", details={"allowed": list(WORKER_STATES)}
            )
        return await self.update_by_pk(worker_id, state=state) > 0

    async def start_drain(self, worker_id: str) -> bool:
        """标记优雅下线：不再领新任务，但允许跑完手头的。"""
        return await self.update_by_pk(worker_id, drain=True, state="draining") > 0

    async def list_alive(
        self,
        *,
        threshold_s: int = DEFAULT_HEARTBEAT_INTERVAL_S * 3,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[Worker]:
        """列出心跳仍在阈值内的 Worker。"""
        moment = now if now is not None else get_clock().now()
        cutoff = moment - timedelta(seconds=threshold_s)
        stmt = (
            select(Worker)
            .where(Worker.last_heartbeat_at >= cutoff)
            .where(Worker.state != "dead")
            .order_by(Worker.last_heartbeat_at.desc())
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_stale(
        self,
        *,
        threshold_s: int = DEFAULT_HEARTBEAT_INTERVAL_S * 3,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[Worker]:
        """列出心跳超时但还没被标记为 ``dead`` 的 Worker（Watchdog 的输入）。"""
        moment = now if now is not None else get_clock().now()
        cutoff = moment - timedelta(seconds=threshold_s)
        stmt = (
            select(Worker)
            .where(Worker.last_heartbeat_at < cutoff)
            .where(Worker.state != "dead")
            .order_by(Worker.last_heartbeat_at.asc())
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_dead(self, worker_id: str) -> bool:
        """标记 Worker 失联。租约的回收由 ``lease_repo`` 单独负责。"""
        return (
            await self.update_by_pk(
                worker_id, state="dead", current_task_id=None, drain=True
            )
            > 0
        )

    async def count_by_state(self) -> dict[str, int]:
        """按状态统计 Worker 数量。"""
        stmt = select(Worker.state, func.count()).group_by(Worker.state)
        result = await self.session.execute(stmt)
        counts = {state: 0 for state in WORKER_STATES}
        for state, count in result.all():
            counts[str(state)] = int(count)
        return counts


class ProviderConfigRepository(BaseRepository[ProviderConfig]):
    """``provider_configs`` 表仓储。"""

    model = ProviderConfig

    async def get_by_name(self, name: str) -> ProviderConfig | None:
        """按 provider 名取配置。"""
        return await self.find_one_by(name=name)

    async def get_by_name_or_raise(self, name: str) -> ProviderConfig:
        """按 provider 名取配置，不存在则抛错。

        :raises NotFoundError: provider 不存在。
        """
        found = await self.get_by_name(name)
        if found is None:
            raise NotFoundError(
                f"Provider {name!r} 未配置", details={"provider": name}
            )
        return found

    async def upsert(
        self,
        name: str,
        *,
        kind: str = "openai_compatible",
        base_url: str | None = None,
        api_key_enc: str | None = None,
        default_model: str | None = None,
        options: dict[str, Any] | None = None,
        enabled: bool = True,
        priority: int = 100,
        failover_to: str | None = None,
    ) -> ProviderConfig:
        """按 ``name`` 幂等 upsert（种子数据与配置热更新共用）。

        .. note::
           ``api_key_enc`` 传 ``None`` 时**保留原密文**，避免热更新配置的时候
           不小心把已保存的密钥抹掉。要清空必须显式传 ``""``。
        """
        existing = await self.get_by_name(name)
        if existing is None:
            return await self.create(
                name=name,
                kind=kind,
                base_url=base_url,
                api_key_enc=api_key_enc,
                default_model=default_model,
                options=dict(options) if options else {},
                enabled=enabled,
                priority=priority,
                failover_to=failover_to,
            )
        existing.kind = kind
        existing.base_url = base_url
        if api_key_enc is not None:
            existing.api_key_enc = api_key_enc or None
        existing.default_model = default_model
        existing.options = dict(options) if options else {}
        existing.enabled = enabled
        existing.priority = priority
        existing.failover_to = failover_to
        await self.session.flush()
        return existing

    async def list_enabled(self, *, limit: int | None = None) -> list[ProviderConfig]:
        """按优先级升序列出启用中的 provider（故障转移链的候选顺序）。"""
        stmt = (
            select(ProviderConfig)
            .where(ProviderConfig.enabled.is_(True))
            .order_by(ProviderConfig.priority.asc(), ProviderConfig.name.asc())
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_enabled(self, name: str, enabled: bool) -> bool:
        """启用 / 停用一个 provider。"""
        stmt = (
            update(ProviderConfig)
            .where(ProviderConfig.name == name)
            .values(enabled=enabled, updated_at=utcnow())
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0) > 0

    async def failover_chain(self, start: str, *, max_hops: int = 8) -> list[str]:
        """沿 ``failover_to`` 展开故障转移链。

        链上遇到环或超过 ``max_hops`` 就停止 —— 配置写错不应该让调度器死循环。

        :param start: 起始 provider 名。
        :param max_hops: 最大跳数。
        :returns: 去重后的 provider 名列表，第一个元素恒为 ``start``。
        """
        chain: list[str] = []
        seen: set[str] = set()
        cursor: str | None = start
        while cursor is not None and cursor not in seen and len(chain) < max_hops:
            chain.append(cursor)
            seen.add(cursor)
            row = await self.get_by_name(cursor)
            cursor = row.failover_to if row is not None else None
        return chain


class AgentRoleRepository(BaseRepository[AgentRole]):
    """``agent_roles`` 表仓储。"""

    model = AgentRole

    async def get_by_name(self, name: RoleName | str) -> AgentRole | None:
        """按角色名取绑定。"""
        role = RoleName(name) if not isinstance(name, RoleName) else name
        return await self.find_one_by(name=role)

    async def get_by_name_or_raise(self, name: RoleName | str) -> AgentRole:
        """按角色名取绑定，不存在则抛错。

        :raises NotFoundError: 角色未配置。
        """
        found = await self.get_by_name(name)
        if found is None:
            raise NotFoundError(f"角色 {name} 未配置", details={"role": str(name)})
        return found

    async def upsert(
        self,
        name: RoleName | str,
        *,
        provider_config_id: str | None = None,
        model: str = "",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        allowed_tools: list[str] | None = None,
        prompt_version: str = "v1",
        enabled: bool = True,
    ) -> AgentRole:
        """按角色名幂等 upsert。"""
        role = RoleName(name) if not isinstance(name, RoleName) else name
        tools = list(allowed_tools) if allowed_tools else []
        existing = await self.get_by_name(role)
        if existing is None:
            return await self.create(
                name=role,
                provider_config_id=provider_config_id,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                allowed_tools=tools,
                prompt_version=prompt_version,
                enabled=enabled,
            )
        existing.provider_config_id = provider_config_id
        existing.model = model
        existing.temperature = temperature
        existing.max_tokens = max_tokens
        existing.allowed_tools = tools
        existing.prompt_version = prompt_version
        existing.enabled = enabled
        await self.session.flush()
        return existing

    async def list_enabled(self) -> list[AgentRole]:
        """列出全部启用中的角色。"""
        stmt = (
            select(AgentRole)
            .where(AgentRole.enabled.is_(True))
            .order_by(AgentRole.name.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def missing_roles(self) -> list[RoleName]:
        """返回尚未配置的角色 —— 首次启动自检用。"""
        configured = {row.name for row in await self.list_enabled()}
        return [role for role in RoleName if role not in configured]


class UserRepository(BaseRepository[User]):
    """``users`` 表仓储。"""

    model = User

    async def get_by_username(self, username: str) -> User | None:
        """按用户名取用户。"""
        return await self.find_one_by(username=username)

    async def create_user(
        self,
        *,
        username: str,
        hashed_password: str,
        role: str = "viewer",
        active: bool = True,
    ) -> User:
        """创建用户。

        :raises ValidationError: 角色非法或用户名已存在。
        """
        if role not in USER_ROLES:
            raise ValidationError(
                f"非法用户角色 {role!r}", details={"allowed": list(USER_ROLES)}
            )
        if await self.get_by_username(username) is not None:
            raise ValidationError(
                f"用户名 {username!r} 已存在", details={"username": username}
            )
        return await self.create(
            username=username,
            hashed_password=hashed_password,
            role=role,
            active=active,
        )

    async def ensure_user(
        self,
        *,
        username: str,
        hashed_password: str,
        role: str = "admin",
    ) -> tuple[User, bool]:
        """存在即返回、不存在则创建（bootstrap 用）。

        :returns: ``(user, created)``。
        """
        existing = await self.get_by_username(username)
        if existing is not None:
            return existing, False
        created = await self.create_user(
            username=username, hashed_password=hashed_password, role=role
        )
        return created, True

    async def set_password(self, user_id: str, hashed_password: str) -> bool:
        """改密（只接受已哈希的值）。"""
        return (
            await self.update_by_pk(
                user_id, hashed_password=hashed_password, updated_at=utcnow()
            )
            > 0
        )

    async def set_active(self, user_id: str, active: bool) -> bool:
        """启用 / 禁用用户。"""
        return await self.update_by_pk(user_id, active=active, updated_at=utcnow()) > 0

    async def list_admins(self) -> list[User]:
        """列出全部启用中的管理员。"""
        stmt = (
            select(User)
            .where(User.role == "admin")
            .where(User.active.is_(True))
            .order_by(User.username.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_active(self) -> int:
        """启用中的用户总数。"""
        stmt = select(func.count()).select_from(User).where(User.active.is_(True))
        result = await self.session.execute(stmt)
        return int(result.scalar_one())


class ApiKeyRepository(BaseRepository[ApiKey]):
    """``api_keys`` 表仓储。

    认证热路径只做**一次**按 ``prefix`` 的唯一索引查找，再在应用层比对
    ``hashed_secret`` —— 绝不用 secret 本身做查询条件，避免明文进 SQL 日志。
    """

    model = ApiKey

    async def get_by_prefix(self, prefix: str) -> ApiKey | None:
        """按公开前缀定位一把 Key。"""
        return await self.find_one_by(prefix=prefix)

    async def issue(
        self,
        *,
        prefix: str,
        hashed_secret: str,
        name: str = "",
        scopes: list[str] | None = None,
        user_id: str | None = None,
        ttl_days: int | None = None,
    ) -> ApiKey:
        """签发一把新 Key（明文 secret 由调用方生成并只返回一次）。

        :param ttl_days: 有效期天数；``None`` 表示永不过期。
        :raises ValidationError: 前缀重复。
        """
        if await self.get_by_prefix(prefix) is not None:
            raise ValidationError(
                f"API Key 前缀 {prefix!r} 冲突，请重新生成", details={"prefix": prefix}
            )
        expires_at: datetime | None = None
        if ttl_days is not None and ttl_days > 0:
            expires_at = utcnow() + timedelta(days=ttl_days)
        return await self.create(
            prefix=prefix,
            hashed_secret=hashed_secret,
            name=name,
            scopes=list(scopes) if scopes else [],
            user_id=user_id,
            expires_at=expires_at,
        )

    async def touch_last_used(self, key_id: str) -> bool:
        """更新最后使用时间（认证成功后调用）。"""
        return await self.update_by_pk(key_id, last_used_at=utcnow()) > 0

    async def revoke(self, key_id: str) -> bool:
        """吊销一把 Key。"""
        return await self.update_by_pk(key_id, revoked=True) > 0

    async def list_for_user(
        self, user_id: str, *, include_revoked: bool = False
    ) -> list[ApiKey]:
        """列出某用户的全部 Key。"""
        stmt = select(ApiKey).where(ApiKey.user_id == user_id)
        if not include_revoked:
            stmt = stmt.where(ApiKey.revoked.is_(False))
        stmt = stmt.order_by(ApiKey.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        """把已过期的 Key 批量标记为吊销。

        :returns: 受影响行数。
        """
        moment = now if now is not None else get_clock().now()
        stmt = (
            update(ApiKey)
            .where(ApiKey.revoked.is_(False))
            .where(ApiKey.expires_at.is_not(None))
            .where(ApiKey.expires_at <= moment)
            .values(revoked=True)
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)


class EvolutionItemRepository(BaseRepository[EvolutionItem]):
    """``evolution_items`` 表仓储 —— 自进化候选项的去重与晋升。"""

    model = EvolutionItem

    async def ingest(
        self,
        *,
        raw: str,
        normalized: str = "",
        source: str = "review",
        run_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> EvolutionItem:
        """摄入一条候选项，自动计算 SimHash。

        :param raw: 原始文本。
        :param normalized: 归一化文本；为空时退化为 ``raw``。
        :param source: 来源（review / failure / user / metric）。
        :param run_id: 关联 Run。
        :param meta: 扩展元数据。
        """
        text = normalized or raw
        return await self.create(
            run_id=run_id,
            source=source,
            raw=raw,
            normalized=text,
            simhash=format(simhash64(text), "016x"),
            score=0.0,
            state="new",
            meta=dict(meta) if meta else {},
        )

    async def list_by_state(
        self, state: str, *, limit: int | None = None
    ) -> list[EvolutionItem]:
        """按状态列出候选项（分值降序）。

        :raises ValidationError: 状态非法。
        """
        if state not in EVOLUTION_STATES:
            raise ValidationError(
                f"非法自进化状态 {state!r}", details={"allowed": list(EVOLUTION_STATES)}
            )
        stmt = (
            select(EvolutionItem)
            .where(EvolutionItem.state == state)
            .order_by(EvolutionItem.score.desc(), EvolutionItem.created_at.asc())
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_cluster(self, item_id: str, cluster_id: str) -> bool:
        """把候选项归入某个聚类。"""
        return (
            await self.update_by_pk(item_id, cluster_id=cluster_id, state="clustered")
            > 0
        )

    async def score(self, item_id: str, value: float) -> bool:
        """写入价值评分。

        :raises ValidationError: 分值超出 ``[0, 1]``。
        """
        if not 0.0 <= value <= 1.0:
            raise ValidationError(
                f"评分必须落在 [0,1]，收到 {value}", details={"score": value}
            )
        return await self.update_by_pk(item_id, score=value, state="scored") > 0

    async def promote(self, item_id: str, task_id: str) -> bool:
        """晋升为正式 Task。"""
        return (
            await self.update_by_pk(item_id, promoted_task_id=task_id, state="promoted")
            > 0
        )

    async def reject(self, item_id: str, *, duplicate: bool = False) -> bool:
        """驳回候选项（``duplicate=True`` 表示近重复）。"""
        state = "duplicate" if duplicate else "rejected"
        return await self.update_by_pk(item_id, state=state) > 0

    async def find_near_duplicates(
        self, simhash_hex: str, *, max_distance: int = 3, limit: int | None = None
    ) -> list[EvolutionItem]:
        """在候选池里找近重复项。

        SimHash 的汉明距离没法用 SQL 索引加速，所以这里先把「未驳回」的候选项
        拉回来在 Python 侧算距离。候选池按设计上限在千级，成本可接受。

        :param simhash_hex: 目标 SimHash 的 16 位 hex。
        :param max_distance: 汉明距离阈值。
        :param limit: 扫描上限。
        """
        target = int(simhash_hex, 16)
        stmt = (
            select(EvolutionItem)
            .where(EvolutionItem.state.notin_(("rejected", "duplicate")))
            .order_by(EvolutionItem.created_at.desc())
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        matched: list[EvolutionItem] = []
        for row in result.scalars().all():
            if not row.simhash:
                continue
            distance = bin(int(row.simhash, 16) ^ target).count("1")
            if distance <= max_distance:
                matched.append(row)
        return matched


class WebhookDeliveryRepository(BaseRepository[WebhookDelivery]):
    """``webhook_deliveries`` 表仓储 —— 靠 ``(provider, delivery_id)`` 唯一键去重。"""

    model = WebhookDelivery

    async def get_by_delivery(
        self, provider: str, delivery_id: str
    ) -> WebhookDelivery | None:
        """按外部投递 ID 查记录。"""
        stmt = (
            select(WebhookDelivery)
            .where(WebhookDelivery.provider == provider)
            .where(WebhookDelivery.delivery_id == delivery_id)
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def record(
        self,
        *,
        provider: str,
        delivery_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        signature_valid: bool = False,
    ) -> tuple[WebhookDelivery, bool]:
        """幂等落库一次投递。

        :returns: ``(delivery, is_new)``；``is_new=False`` 表示重复投递，
            调用方应直接短路返回 200，不要重复触发业务。
        """
        existing = await self.get_by_delivery(provider, delivery_id)
        if existing is not None:
            return existing, False
        created = await self.create(
            provider=provider,
            delivery_id=delivery_id,
            event_type=event_type,
            payload=dict(payload) if payload else {},
            signature_valid=signature_valid,
            processed=False,
        )
        return created, True

    async def mark_processed(
        self, row_id: str, *, error: str | None = None
    ) -> bool:
        """标记投递已处理（``error`` 非空表示处理失败但不再重试）。"""
        return (
            await self.update_by_pk(
                row_id, processed=True, error=error, processed_at=utcnow()
            )
            > 0
        )

    async def list_unprocessed(self, *, limit: int | None = None) -> list[WebhookDelivery]:
        """列出待处理的投递（按接收时间升序）。"""
        stmt = (
            select(WebhookDelivery)
            .where(WebhookDelivery.processed.is_(False))
            .order_by(WebhookDelivery.received_at.asc())
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
