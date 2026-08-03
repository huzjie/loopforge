"""hot-path composite indexes + partial unique indexes

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-05

本迁移只做一件事：把**热点查询路径**与**并发正确性**依赖的索引一次性建齐。

其中三个是**部分索引（partial index）**，是 LoopForge 若干条铁律的物理实现：

* ``uq_leases_active_task`` —— 「同一个 Task 在任意时刻至多一个 ACTIVE 租约」。
  这不是应用层的检查，而是数据库的唯一约束：即使调度器出现脑裂、两个 Worker
  同时尝试获取同一任务，第二个也会在 INSERT 时撞上唯一冲突而失败。
  **零重复获取**这条铁律，最终就落在这一行 DDL 上。
* ``ix_steps_unconfirmed`` —— 只索引 ``INTENT_RECORDED`` 的步骤。
  未确认步骤在健康系统里是极少数，对它们建全表索引纯属浪费；而崩溃恢复时
  又必须能瞬间捞出全部未确认步骤做 probe。
* ``ix_memory_compressed`` —— 只索引未被压缩的记忆条目，理由同上。

SQLite 与 PostgreSQL 的部分索引语法都写作 ``WHERE <predicate>``，但 SQLAlchemy
需要用方言前缀参数分别声明；两个参数同时传入，各自方言只认自己那个。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 「一个任务至多一个活跃租约」的谓词。
ACTIVE_LEASE_PREDICATE = "state = 'ACTIVE'"

#: 「未确认步骤」的谓词（对应 StepState.INTENT_RECORDED）。
UNCONFIRMED_STEP_PREDICATE = "state = 'INTENT_RECORDED'"

#: 「尚未被压缩进摘要的记忆」的谓词。
UNCOMPRESSED_MEMORY_PREDICATE = "compressed_into IS NULL"


def upgrade() -> None:
    """建立热点复合索引与部分索引。"""
    # ---- 热点复合索引 --------------------------------------------------
    op.create_index("ix_runs_active", "runs", ["state", "updated_at"], unique=False)
    op.create_index("ix_events_run_seq", "events", ["run_id", "seq"], unique=False)
    op.create_index(
        "ix_checkpoints_latest", "checkpoints", ["run_id", "created_at"], unique=False
    )
    op.create_index("ix_cost_run_time", "cost_records", ["run_id", "occurred_at"], unique=False)
    op.create_index(
        "ix_approvals_state_expires", "approvals", ["state", "expires_at"], unique=False
    )
    op.create_index("ix_workers_state_hb", "workers", ["state", "last_heartbeat_at"], unique=False)
    op.create_index("ix_evo_state_score", "evolution_items", ["state", "score"], unique=False)
    op.create_index(
        "ix_webhook_processed", "webhook_deliveries", ["processed", "received_at"], unique=False
    )
    op.create_index("ix_leases_expiry", "leases", ["state", "expires_at"], unique=False)
    op.create_index("ix_tasks_leasable", "tasks", ["state", "created_at"], unique=False)

    # ---- 部分索引：并发正确性的物理保证 --------------------------------
    op.create_index(
        "uq_leases_active_task",
        "leases",
        ["task_id"],
        unique=True,
        sqlite_where=sa.text(ACTIVE_LEASE_PREDICATE),
        postgresql_where=sa.text(ACTIVE_LEASE_PREDICATE),
    )
    op.create_index(
        "ix_steps_unconfirmed",
        "steps",
        ["state", "intent_at"],
        unique=False,
        sqlite_where=sa.text(UNCONFIRMED_STEP_PREDICATE),
        postgresql_where=sa.text(UNCONFIRMED_STEP_PREDICATE),
    )
    op.create_index(
        "ix_memory_compressed",
        "memory_items",
        ["run_id", "layer"],
        unique=False,
        sqlite_where=sa.text(UNCOMPRESSED_MEMORY_PREDICATE),
        postgresql_where=sa.text(UNCOMPRESSED_MEMORY_PREDICATE),
    )


def downgrade() -> None:
    """逆序删除索引。"""
    op.drop_index("ix_memory_compressed", table_name="memory_items")
    op.drop_index("ix_steps_unconfirmed", table_name="steps")
    op.drop_index("uq_leases_active_task", table_name="leases")

    op.drop_index("ix_tasks_leasable", table_name="tasks")
    op.drop_index("ix_leases_expiry", table_name="leases")
    op.drop_index("ix_webhook_processed", table_name="webhook_deliveries")
    op.drop_index("ix_evo_state_score", table_name="evolution_items")
    op.drop_index("ix_workers_state_hb", table_name="workers")
    op.drop_index("ix_approvals_state_expires", table_name="approvals")
    op.drop_index("ix_cost_run_time", table_name="cost_records")
    op.drop_index("ix_checkpoints_latest", table_name="checkpoints")
    op.drop_index("ix_events_run_seq", table_name="events")
    op.drop_index("ix_runs_active", table_name="runs")
