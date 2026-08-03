"""Unit of Work —— 把「一次业务操作 = 一个事务 + 一组仓储」封装起来。

来源：``docs/TASK_BREAKDOWN.md`` §3.1.3、``docs/ARCHITECTURE.md`` §4.1。

典型用法::

    async with UnitOfWork() as uow:
        run = await uow.runs.get_or_raise(run_id)
        task = await uow.tasks.create(run_id=run.id, task_key="T-001", title="…")
        uow.collect_event(EventType.TASK_CREATED, {"task_id": task.id}, run_id=run.id)
    # 退出时：flush → commit → 事务成功后才发布收集到的事件

**为什么事件要延后发布**：事件一旦发出去就收不回来。若在事务内发布而事务随后
回滚，订阅者会看到「幽灵事件」。所以 UoW 把事件缓存在内存里，只有 commit 真正
成功之后才交给 EventBus。这是 ARCHITECTURE §4.1「事务边界与事件边界必须对齐」
的落地。
"""

from __future__ import annotations

import types
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Self

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from loopforge.core.enums import Severity
from loopforge.core.errors import DatabaseError
from loopforge.core.event_bus import EventBus, get_event_bus
from loopforge.core.event_types import EventType
from loopforge.db.session import get_session_factory, translate_db_error

__all__ = [
    "GovernanceRepos",
    "PendingEvent",
    "SystemRepos",
    "UnitOfWork",
    "transactional",
]


class GovernanceRepos:
    """``uow.governance`` —— 治理域仓储门面。

    ``TASK_BREAKDOWN.md`` §11.2 把 UoW 的仓储属性名冻结为
    ``projects / runs / tasks / leases / steps / events / memory / governance / system``。
    治理域内部拆成了预算 / 策略 / 审批 / 成本 / 审计五个仓储，这里按冻结名聚合，
    下游一律走 ``uow.governance.budgets`` 这种两段式路径。
    """

    __slots__ = ("_uow",)

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    @property
    def budgets(self) -> Any:
        """:class:`~loopforge.db.repositories.governance_repo.BudgetRepository`。"""
        return self._uow.budgets

    @property
    def policies(self) -> Any:
        """:class:`~loopforge.db.repositories.governance_repo.PolicyRepository`。"""
        return self._uow.policies

    @property
    def approvals(self) -> Any:
        """:class:`~loopforge.db.repositories.governance_repo.ApprovalRepository`。"""
        return self._uow.approvals

    @property
    def costs(self) -> Any:
        """:class:`~loopforge.db.repositories.governance_repo.CostRecordRepository`。"""
        return self._uow.costs

    @property
    def audit_logs(self) -> Any:
        """:class:`~loopforge.db.repositories.event_repo.AuditLogRepository`。"""
        return self._uow.audit_logs

    def __repr__(self) -> str:
        return "GovernanceRepos(budgets, policies, approvals, costs, audit_logs)"


class SystemRepos:
    """``uow.system`` —— 系统域仓储门面（§11.2 冻结名）。

    聚合 Worker / Provider 配置 / 角色 / 用户 / API Key / 演化项 / Webhook 投递。
    """

    __slots__ = ("_uow",)

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    @property
    def workers(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.WorkerRepository`。"""
        return self._uow.workers

    @property
    def providers(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.ProviderConfigRepository`。"""
        return self._uow.providers

    @property
    def agent_roles(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.AgentRoleRepository`。"""
        return self._uow.agent_roles

    @property
    def users(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.UserRepository`。"""
        return self._uow.users

    @property
    def api_keys(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.ApiKeyRepository`。"""
        return self._uow.api_keys

    @property
    def evolution(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.EvolutionItemRepository`。"""
        return self._uow.evolution

    @property
    def webhooks(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.WebhookDeliveryRepository`。"""
        return self._uow.webhooks

    def __repr__(self) -> str:
        return (
            "SystemRepos(workers, providers, agent_roles, users, "
            "api_keys, evolution, webhooks)"
        )


@dataclass(slots=True)
class PendingEvent:
    """事务提交成功后才会真正发布的事件。"""

    event_type: EventType
    payload: dict[str, Any]
    run_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    severity: Severity = Severity.INFO
    trace_id: str | None = None


@dataclass
class UnitOfWork:
    """异步 Unit of Work。

    :param session_factory: session 构造器；``None`` 表示使用全局工厂。
    :param event_bus: 事件总线；``None`` 表示使用全局单例。
    :param autocommit: 无异常退出时是否自动 commit。默认 ``True``。
    """

    session_factory: Callable[[], AsyncSession] | None = None
    event_bus: EventBus | None = None
    autocommit: bool = True

    session: AsyncSession | None = field(default=None, init=False, repr=False)
    pending_events: list[PendingEvent] = field(default_factory=list, init=False, repr=False)
    committed: bool = field(default=False, init=False)
    rolled_back: bool = field(default=False, init=False)

    _repos: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    # -- 生命周期 -----------------------------------------------------------

    async def __aenter__(self) -> Self:
        """打开 session 并重置状态。"""
        make = self.session_factory if self.session_factory is not None else get_session_factory()
        self.session = make()
        self.pending_events.clear()
        self._repos.clear()
        self.committed = False
        self.rolled_back = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> bool:
        """无异常且 ``autocommit`` 为真则提交，否则回滚；最后一定关闭 session。"""
        if self.session is None:
            return False
        try:
            if exc_type is None and self.autocommit:
                await self.commit()
            elif exc_type is not None:
                await self.rollback()
        finally:
            await self.session.close()
            self.session = None
        return False

    # -- 事务操作 -----------------------------------------------------------

    def _require_session(self) -> AsyncSession:
        """返回当前 session；未进入上下文时抛错。"""
        if self.session is None:
            raise DatabaseError(
                "UnitOfWork 未激活：请在 `async with UnitOfWork() as uow:` 内使用"
            )
        return self.session

    async def flush(self) -> None:
        """把挂起的 ORM 变更刷到数据库（不提交），用于提前拿自增主键。"""
        session = self._require_session()
        try:
            await session.flush()
        except SQLAlchemyError as db_exc:
            raise translate_db_error(db_exc) from db_exc

    async def commit(self) -> None:
        """提交事务，成功后发布所有缓存事件。"""
        session = self._require_session()
        try:
            await session.commit()
        except SQLAlchemyError as db_exc:
            await session.rollback()
            self.rolled_back = True
            self.pending_events.clear()
            raise translate_db_error(db_exc) from db_exc
        self.committed = True
        await self._dispatch_events()

    async def rollback(self) -> None:
        """回滚事务并丢弃所有缓存事件。"""
        session = self._require_session()
        await session.rollback()
        self.rolled_back = True
        self.pending_events.clear()

    async def refresh(self, instance: Any) -> None:
        """从数据库重新加载实例属性。"""
        session = self._require_session()
        await session.refresh(instance)

    # -- 事件 ---------------------------------------------------------------

    def collect_event(
        self,
        event_type: EventType,
        payload: dict[str, Any] | None = None,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        step_id: str | None = None,
        severity: Severity = Severity.INFO,
        trace_id: str | None = None,
    ) -> PendingEvent:
        """缓存一个事件，等 commit 成功后再发布。

        :returns: 被缓存的 :class:`PendingEvent`，便于调用方断言。
        """
        pending = PendingEvent(
            event_type=event_type,
            payload=dict(payload) if payload else {},
            run_id=run_id,
            task_id=task_id,
            step_id=step_id,
            severity=severity,
            trace_id=trace_id,
        )
        self.pending_events.append(pending)
        return pending

    async def _dispatch_events(self) -> None:
        """把缓存事件按顺序交给 EventBus，然后清空缓存。"""
        if not self.pending_events:
            return
        bus = self.event_bus if self.event_bus is not None else get_event_bus()
        drained = list(self.pending_events)
        self.pending_events.clear()
        for pending in drained:
            await bus.publish(
                pending.event_type,
                pending.payload,
                run_id=pending.run_id,
                task_id=pending.task_id,
                step_id=pending.step_id,
                severity=pending.severity,
                trace_id=pending.trace_id,
            )

    # -- 仓储惰性属性 -------------------------------------------------------

    def _repo(self, key: str, factory: Callable[[AsyncSession], Any]) -> Any:
        """惰性构造并缓存仓储实例。"""
        cached = self._repos.get(key)
        if cached is None:
            cached = factory(self._require_session())
            self._repos[key] = cached
        return cached

    @property
    def projects(self) -> Any:
        """:class:`~loopforge.db.repositories.project_repo.ProjectRepository`。"""
        from loopforge.db.repositories.project_repo import ProjectRepository

        return self._repo("projects", ProjectRepository)

    @property
    def repos(self) -> Any:
        """:class:`~loopforge.db.repositories.project_repo.RepoRepository`。"""
        from loopforge.db.repositories.project_repo import RepoRepository

        return self._repo("repos", RepoRepository)

    @property
    def runs(self) -> Any:
        """:class:`~loopforge.db.repositories.run_repo.RunRepository`。"""
        from loopforge.db.repositories.run_repo import RunRepository

        return self._repo("runs", RunRepository)

    @property
    def tasks(self) -> Any:
        """:class:`~loopforge.db.repositories.task_repo.TaskRepository`。"""
        from loopforge.db.repositories.task_repo import TaskRepository

        return self._repo("tasks", TaskRepository)

    @property
    def leases(self) -> Any:
        """:class:`~loopforge.db.repositories.lease_repo.LeaseRepository`。"""
        from loopforge.db.repositories.lease_repo import LeaseRepository

        return self._repo("leases", LeaseRepository)

    @property
    def steps(self) -> Any:
        """:class:`~loopforge.db.repositories.step_repo.StepRepository`。"""
        from loopforge.db.repositories.step_repo import StepRepository

        return self._repo("steps", StepRepository)

    @property
    def checkpoints(self) -> Any:
        """:class:`~loopforge.db.repositories.step_repo.CheckpointRepository`。"""
        from loopforge.db.repositories.step_repo import CheckpointRepository

        return self._repo("checkpoints", CheckpointRepository)

    @property
    def idempotency(self) -> Any:
        """:class:`~loopforge.db.repositories.step_repo.IdempotencyRepository`。"""
        from loopforge.db.repositories.step_repo import IdempotencyRepository

        return self._repo("idempotency", IdempotencyRepository)

    @property
    def events(self) -> Any:
        """:class:`~loopforge.db.repositories.event_repo.EventRepository`。"""
        from loopforge.db.repositories.event_repo import EventRepository

        return self._repo("events", EventRepository)

    @property
    def audit_logs(self) -> Any:
        """:class:`~loopforge.db.repositories.event_repo.AuditLogRepository`。"""
        from loopforge.db.repositories.event_repo import AuditLogRepository

        return self._repo("audit_logs", AuditLogRepository)

    @property
    def memory(self) -> Any:
        """:class:`~loopforge.db.repositories.memory_repo.MemoryRepository`。"""
        from loopforge.db.repositories.memory_repo import MemoryRepository

        return self._repo("memory", MemoryRepository)

    @property
    def budgets(self) -> Any:
        """:class:`~loopforge.db.repositories.governance_repo.BudgetRepository`。"""
        from loopforge.db.repositories.governance_repo import BudgetRepository

        return self._repo("budgets", BudgetRepository)

    @property
    def policies(self) -> Any:
        """:class:`~loopforge.db.repositories.governance_repo.PolicyRepository`。"""
        from loopforge.db.repositories.governance_repo import PolicyRepository

        return self._repo("policies", PolicyRepository)

    @property
    def approvals(self) -> Any:
        """:class:`~loopforge.db.repositories.governance_repo.ApprovalRepository`。"""
        from loopforge.db.repositories.governance_repo import ApprovalRepository

        return self._repo("approvals", ApprovalRepository)

    @property
    def workers(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.WorkerRepository`。"""
        from loopforge.db.repositories.system_repo import WorkerRepository

        return self._repo("workers", WorkerRepository)

    @property
    def providers(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.ProviderConfigRepository`。"""
        from loopforge.db.repositories.system_repo import ProviderConfigRepository

        return self._repo("providers", ProviderConfigRepository)

    @property
    def agent_roles(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.AgentRoleRepository`。"""
        from loopforge.db.repositories.system_repo import AgentRoleRepository

        return self._repo("agent_roles", AgentRoleRepository)

    @property
    def users(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.UserRepository`。"""
        from loopforge.db.repositories.system_repo import UserRepository

        return self._repo("users", UserRepository)

    @property
    def api_keys(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.ApiKeyRepository`。"""
        from loopforge.db.repositories.system_repo import ApiKeyRepository

        return self._repo("api_keys", ApiKeyRepository)

    @property
    def milestones(self) -> Any:
        """:class:`~loopforge.db.repositories.memory_repo.MilestoneSummaryRepository`。"""
        from loopforge.db.repositories.memory_repo import MilestoneSummaryRepository

        return self._repo("milestones", MilestoneSummaryRepository)

    @property
    def pinned(self) -> Any:
        """:class:`~loopforge.db.repositories.memory_repo.PinnedConstraintRepository`。"""
        from loopforge.db.repositories.memory_repo import PinnedConstraintRepository

        return self._repo("pinned", PinnedConstraintRepository)

    @property
    def index(self) -> Any:
        """:class:`~loopforge.db.repositories.memory_repo.InvertedIndexRepository`。"""
        from loopforge.db.repositories.memory_repo import InvertedIndexRepository

        return self._repo("index", InvertedIndexRepository)

    @property
    def costs(self) -> Any:
        """:class:`~loopforge.db.repositories.governance_repo.CostRecordRepository`。"""
        from loopforge.db.repositories.governance_repo import CostRecordRepository

        return self._repo("costs", CostRecordRepository)

    @property
    def llm_calls(self) -> Any:
        """:class:`~loopforge.db.repositories.step_repo.LlmCallRepository`。"""
        from loopforge.db.repositories.step_repo import LlmCallRepository

        return self._repo("llm_calls", LlmCallRepository)

    @property
    def tool_calls(self) -> Any:
        """:class:`~loopforge.db.repositories.step_repo.ToolCallRepository`。"""
        from loopforge.db.repositories.step_repo import ToolCallRepository

        return self._repo("tool_calls", ToolCallRepository)

    @property
    def artifacts(self) -> Any:
        """:class:`~loopforge.db.repositories.step_repo.ArtifactRepository`。"""
        from loopforge.db.repositories.step_repo import ArtifactRepository

        return self._repo("artifacts", ArtifactRepository)

    @property
    def evolution(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.EvolutionItemRepository`。"""
        from loopforge.db.repositories.system_repo import EvolutionItemRepository

        return self._repo("evolution", EvolutionItemRepository)

    @property
    def webhooks(self) -> Any:
        """:class:`~loopforge.db.repositories.system_repo.WebhookDeliveryRepository`。"""
        from loopforge.db.repositories.system_repo import WebhookDeliveryRepository

        return self._repo("webhooks", WebhookDeliveryRepository)

    # -- §11.2 冻结的域门面 -------------------------------------------------

    @property
    def governance(self) -> GovernanceRepos:
        """治理域门面：``budgets`` / ``policies`` / ``approvals`` / ``costs`` / ``audit_logs``。"""
        return self._repo("governance", lambda _session: GovernanceRepos(self))

    @property
    def system(self) -> SystemRepos:
        """系统域门面：``workers`` / ``providers`` / ``agent_roles`` / ``users`` / ``api_keys`` / ``evolution`` / ``webhooks``。"""
        return self._repo("system", lambda _session: SystemRepos(self))


def transactional(func: Callable[..., Any]) -> Callable[..., Any]:
    """装饰器：为异步函数自动注入一个 ``uow`` 关键字参数。

    被装饰函数签名须形如 ``async def f(*args, uow: UnitOfWork, **kwargs)``。
    若调用方已显式传入 ``uow``，则直接透传、不再新建事务（支持嵌套调用）。
    """
    import functools

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("uow") is not None:
            return await func(*args, **kwargs)
        async with UnitOfWork() as uow:
            kwargs["uow"] = uow
            return await func(*args, **kwargs)

    return wrapper
