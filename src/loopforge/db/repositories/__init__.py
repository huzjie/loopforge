"""仓储层聚合导出。

来源：``docs/TASK_BREAKDOWN.md`` §3.1.3。

**仓储层的三条纪律**（与 ``base.py`` 的模块 docstring 呼应）：

1. 仓储只做持久化，不发事件、不做业务校验；
2. 仓储不 commit —— 事务边界归 :class:`~loopforge.db.uow.UnitOfWork` 管；
3. 需要并发正确性的写操作一律走 **CAS**（条件 UPDATE + 受影响行数判定），
   绝不用「先 SELECT 再 UPDATE」。

下游 WP 请优先通过 ``uow.<name>`` 属性拿仓储实例，而不是自己 new。
"""

from __future__ import annotations

from loopforge.db.repositories.base import BaseRepository, ModelT, clamp_limit
from loopforge.db.repositories.event_repo import AuditLogRepository, EventRepository
from loopforge.db.repositories.governance_repo import (
    ApprovalRepository,
    BudgetRepository,
    CostRecordRepository,
    PolicyRepository,
)
from loopforge.db.repositories.lease_repo import LeaseRepository
from loopforge.db.repositories.memory_repo import (
    InvertedIndexRepository,
    MemoryRepository,
    MilestoneSummaryRepository,
    PinnedConstraintRepository,
)
from loopforge.db.repositories.project_repo import ProjectRepository, RepoRepository
from loopforge.db.repositories.run_repo import RunRepository
from loopforge.db.repositories.step_repo import (
    ArtifactRepository,
    CheckpointRepository,
    IdempotencyRepository,
    LlmCallRepository,
    StepRepository,
    ToolCallRepository,
)
from loopforge.db.repositories.system_repo import (
    AgentRoleRepository,
    ApiKeyRepository,
    EvolutionItemRepository,
    ProviderConfigRepository,
    UserRepository,
    WebhookDeliveryRepository,
    WorkerRepository,
)
from loopforge.db.repositories.task_repo import CasResult, TaskRepository

__all__ = [
    # 基础设施
    "BaseRepository",
    "ModelT",
    "clamp_limit",
    "CasResult",
    # 项目
    "ProjectRepository",
    "RepoRepository",
    # 编排
    "RunRepository",
    "TaskRepository",
    "LeaseRepository",
    "StepRepository",
    "CheckpointRepository",
    "IdempotencyRepository",
    # 可观测
    "EventRepository",
    "AuditLogRepository",
    "LlmCallRepository",
    "ToolCallRepository",
    "ArtifactRepository",
    # 记忆
    "MemoryRepository",
    "MilestoneSummaryRepository",
    "PinnedConstraintRepository",
    "InvertedIndexRepository",
    # 治理
    "BudgetRepository",
    "CostRecordRepository",
    "PolicyRepository",
    "ApprovalRepository",
    # 系统
    "WorkerRepository",
    "ProviderConfigRepository",
    "AgentRoleRepository",
    "UserRepository",
    "ApiKeyRepository",
    "EvolutionItemRepository",
    "WebhookDeliveryRepository",
    # 辅助
    "ALL_REPOSITORIES",
]

#: 全部仓储类，供 CI 做「每张表至少有一个仓储」的覆盖率校验。
ALL_REPOSITORIES: tuple[type[BaseRepository[object]], ...] = (  # type: ignore[assignment]
    ProjectRepository,
    RepoRepository,
    RunRepository,
    TaskRepository,
    LeaseRepository,
    StepRepository,
    CheckpointRepository,
    IdempotencyRepository,
    EventRepository,
    AuditLogRepository,
    LlmCallRepository,
    ToolCallRepository,
    ArtifactRepository,
    MemoryRepository,
    MilestoneSummaryRepository,
    PinnedConstraintRepository,
    InvertedIndexRepository,
    BudgetRepository,
    CostRecordRepository,
    PolicyRepository,
    ApprovalRepository,
    WorkerRepository,
    ProviderConfigRepository,
    AgentRoleRepository,
    UserRepository,
    ApiKeyRepository,
    EvolutionItemRepository,
    WebhookDeliveryRepository,
)
