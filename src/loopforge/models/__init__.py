"""LoopForge ORM 模型包 —— 全部 31 张表的聚合导出。

.. note::
   ``docs/ARCHITECTURE.md`` §5 的小标题写「共 26 张表」，但同节 §5.1 的表清单
   逐行列出了 **31 张**。与 §10.3「62 种事件 / 实际列 72 种」同理，**以明细为准**，
   本包按 31 张表实现，并在
   :func:`loopforge.models.table_names` 中提供权威清单。

**导入本模块即完成全部模型注册**，这是 ``alembic/env.py`` 能够正确解析
``Base.metadata`` 的前提；任何新增模型都必须在此处 re-export，否则迁移会漏表。
"""

from __future__ import annotations

from loopforge.db.base import Base, metadata
from loopforge.models.agent_role import AgentRole, ProviderConfig
from loopforge.models.artifact import Artifact
from loopforge.models.auth import USER_ROLES, ApiKey, User
from loopforge.models.budget import Budget, CostRecord
from loopforge.models.call import LlmCall, ToolCall
from loopforge.models.event import AuditLog, Event
from loopforge.models.evolution import (
    EVOLUTION_STATES,
    EvolutionItem,
    WebhookDelivery,
)
from loopforge.models.lease import Lease
from loopforge.models.memory import (
    IndexStats,
    InvertedIndex,
    MemoryItem,
    MemoryLineage,
    MilestoneSummary,
    PinnedConstraint,
)
from loopforge.models.policy import Approval, Policy, PolicyRule
from loopforge.models.project import Project, Repo
from loopforge.models.run import Run
from loopforge.models.step import Checkpoint, IdempotencyRecord, Step
from loopforge.models.task import Task
from loopforge.models.worker import WORKER_STATES, Worker

__all__ = [
    "Base",
    "metadata",
    # 项目与仓库
    "Project",
    "Repo",
    # 编排核心
    "Run",
    "Task",
    "Lease",
    "Step",
    "Checkpoint",
    "IdempotencyRecord",
    # 可观测
    "Event",
    "AuditLog",
    "LlmCall",
    "ToolCall",
    "Artifact",
    # 记忆
    "MemoryItem",
    "MilestoneSummary",
    "PinnedConstraint",
    "MemoryLineage",
    "InvertedIndex",
    "IndexStats",
    # 治理
    "Budget",
    "CostRecord",
    "Policy",
    "PolicyRule",
    "Approval",
    # 系统
    "ProviderConfig",
    "AgentRole",
    "Worker",
    "User",
    "ApiKey",
    # 自进化与外部集成
    "EvolutionItem",
    "WebhookDelivery",
    # 常量
    "USER_ROLES",
    "WORKER_STATES",
    "EVOLUTION_STATES",
    # 辅助
    "ALL_MODELS",
    "table_names",
    "model_by_tablename",
]

#: 全部 ORM 模型类，顺序即建表拓扑顺序（父表在前，便于人工核对外键）。
ALL_MODELS: tuple[type[Base], ...] = (
    Project,
    Repo,
    Run,
    Task,
    Lease,
    Step,
    Checkpoint,
    IdempotencyRecord,
    Event,
    AuditLog,
    LlmCall,
    ToolCall,
    Artifact,
    MilestoneSummary,
    MemoryItem,
    PinnedConstraint,
    MemoryLineage,
    InvertedIndex,
    IndexStats,
    Budget,
    CostRecord,
    Policy,
    PolicyRule,
    Approval,
    ProviderConfig,
    AgentRole,
    Worker,
    User,
    ApiKey,
    EvolutionItem,
    WebhookDelivery,
)


def table_names() -> tuple[str, ...]:
    """返回全部表名（按 :data:`ALL_MODELS` 顺序）。

    :returns: 表名元组，长度应等于 ``len(ALL_MODELS)``。
    """
    return tuple(model.__tablename__ for model in ALL_MODELS)


def model_by_tablename(name: str) -> type[Base]:
    """按表名反查模型类。

    :param name: 表名，如 ``"tasks"``。
    :raises KeyError: 表名不存在时抛出。
    """
    for model in ALL_MODELS:
        if model.__tablename__ == name:
            return model
    raise KeyError(f"未知表名：{name!r}；可选值：{', '.join(table_names())}")
