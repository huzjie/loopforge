"""initial schema: project / repo / run / task / lease / step / checkpoint / idempotency

Revision ID: 0001
Revises:
Create Date: 2026-08-05

编排主干的 8 张表。这里只建「表 + 主键 + 外键 + 唯一约束 + 检查约束 + 基础索引」，
所有热点复合索引与部分唯一索引统一推迟到 ``0006``，理由有二：

1. 建表阶段就挂满索引会让后续几个迁移的批量插入变慢（自托管用户常在
   ``upgrade head`` 之后立刻导入历史数据）；
2. 部分唯一索引（partial unique index）的语法在 SQLite 与 PostgreSQL 上不同，
   集中在一个文件里方便审计「零重复获取」这条铁律到底靠什么保证。

列类型全部使用 :mod:`loopforge.db.types` 里的方言中立类型，而**不是**直接引用
``loopforge.core.enums`` —— 迁移文件必须自包含，枚举成员日后改名不能让历史迁移炸掉。
枚举列一律落成 ``VARCHAR(32)``。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from loopforge.db.types import JsonType, UtcDateTime

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 枚举列的统一宽度（StrEnumType 的 impl 长度）。
ENUM_LEN = 32

#: ULID 主键宽度。
ID_LEN = 26


def upgrade() -> None:
    """建立编排主干 8 张表。"""
    op.create_table(
        "projects",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("settings", JsonType(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.UniqueConstraint("slug", name="uq_projects_slug"),
    )

    op.create_table(
        "repos",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("project_id", sa.String(ID_LEN), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("remote_url", sa.String(500), nullable=True),
        sa.Column("default_branch", sa.String(200), nullable=False),
        sa.Column("credentials_ref", sa.String(200), nullable=True),
        sa.Column("local_path", sa.String(1000), nullable=True),
        sa.Column("labels_config", JsonType(), nullable=False),
        sa.Column("webhook_secret_enc", sa.Text(), nullable=True),
        sa.Column("webhook_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_repos"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_repos_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("project_id", "remote_url", name="uq_repos_project_remote"),
    )
    op.create_index("ix_repos_project_id", "repos", ["project_id"], unique=False)

    op.create_table(
        "runs",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("project_id", sa.String(ID_LEN), nullable=False),
        sa.Column("repo_id", sa.String(ID_LEN), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("constraints", JsonType(), nullable=False),
        sa.Column("state", sa.String(ENUM_LEN), nullable=False),
        sa.Column("loop_index", sa.Integer(), nullable=False),
        sa.Column("milestone_index", sa.Integer(), nullable=False),
        sa.Column("max_loops", sa.Integer(), nullable=False),
        sa.Column("config_snapshot", JsonType(), nullable=False),
        sa.Column("budget_id", sa.String(ID_LEN), nullable=True),
        sa.Column("policy_id", sa.String(ID_LEN), nullable=True),
        sa.Column("recovery_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(ENUM_LEN), nullable=True),
        sa.Column("started_at", UtcDateTime(), nullable=True),
        sa.Column("finished_at", UtcDateTime(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_runs"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_runs_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["repos.id"],
            name="fk_runs_repo_id_repos",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("loop_index >= 0", name="ck_runs_loop_index_non_negative"),
        sa.CheckConstraint("milestone_index >= 0", name="ck_runs_milestone_index_non_negative"),
        sa.CheckConstraint("recovery_count >= 0", name="ck_runs_recovery_count_non_negative"),
        sa.CheckConstraint("max_loops > 0", name="ck_runs_max_loops_positive"),
    )
    op.create_index("ix_runs_project_created", "runs", ["project_id", "created_at"], unique=False)
    op.create_index("ix_runs_state", "runs", ["state"], unique=False)

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("run_id", sa.String(ID_LEN), nullable=False),
        sa.Column("repo_id", sa.String(ID_LEN), nullable=True),
        sa.Column("task_key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("state", sa.String(ENUM_LEN), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("depends_on", JsonType(), nullable=False),
        sa.Column("touch_paths", JsonType(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("reclaim_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("max_reclaims", sa.Integer(), nullable=False),
        sa.Column("current_lease_id", sa.String(ID_LEN), nullable=True),
        sa.Column("fence_token", sa.Integer(), nullable=False),
        sa.Column("external_issue_number", sa.Integer(), nullable=True),
        sa.Column("branch_name", sa.String(300), nullable=True),
        sa.Column("pr_url", sa.String(1000), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("result", JsonType(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name="fk_tasks_run_id_runs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name="fk_tasks_repo_id_repos", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("run_id", "task_key", name="uq_tasks_run_key"),
        sa.UniqueConstraint("repo_id", "external_issue_number", name="uq_tasks_repo_issue"),
        sa.CheckConstraint("priority >= 0", name="ck_tasks_priority_non_negative"),
        sa.CheckConstraint("version >= 0", name="ck_tasks_version_non_negative"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_tasks_attempt_count_non_negative"),
        sa.CheckConstraint("reclaim_count >= 0", name="ck_tasks_reclaim_count_non_negative"),
        sa.CheckConstraint("fence_token >= 0", name="ck_tasks_fence_token_non_negative"),
    )
    op.create_index("ix_tasks_run_state", "tasks", ["run_id", "state"], unique=False)

    op.create_table(
        "leases",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("task_id", sa.String(ID_LEN), nullable=False),
        sa.Column("worker_id", sa.String(ID_LEN), nullable=False),
        sa.Column("fence_token", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(ENUM_LEN), nullable=False),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("max_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("acquired_at", UtcDateTime(), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
        sa.Column("last_heartbeat_at", UtcDateTime(), nullable=False),
        sa.Column("heartbeat_count", sa.Integer(), nullable=False),
        sa.Column("extend_count", sa.Integer(), nullable=False),
        sa.Column("expiry_reason", sa.String(200), nullable=True),
        sa.Column("released_at", UtcDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_leases"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="fk_leases_task_id_tasks", ondelete="CASCADE"
        ),
        sa.CheckConstraint("ttl_seconds > 0", name="ck_leases_ttl_positive"),
        sa.CheckConstraint("max_ttl_seconds >= ttl_seconds", name="ck_leases_max_ttl_ge_ttl"),
        sa.CheckConstraint("fence_token >= 0", name="ck_leases_fence_token_non_negative"),
        sa.CheckConstraint("heartbeat_count >= 0", name="ck_leases_heartbeat_count_non_negative"),
        sa.CheckConstraint("extend_count >= 0", name="ck_leases_extend_count_non_negative"),
    )
    op.create_index("ix_leases_task", "leases", ["task_id"], unique=False)
    op.create_index("ix_leases_worker", "leases", ["worker_id"], unique=False)

    op.create_table(
        "steps",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("run_id", sa.String(ID_LEN), nullable=False),
        sa.Column("task_id", sa.String(ID_LEN), nullable=True),
        sa.Column("loop_index", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(64), nullable=False),
        sa.Column("role", sa.String(ENUM_LEN), nullable=True),
        sa.Column("state", sa.String(ENUM_LEN), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("side_effect_class", sa.String(ENUM_LEN), nullable=False),
        sa.Column("intent_payload", JsonType(), nullable=False),
        sa.Column("result_payload", JsonType(), nullable=False),
        sa.Column("external_ref", sa.String(500), nullable=True),
        sa.Column("error_code", sa.String(ENUM_LEN), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("fence_token", sa.Integer(), nullable=False),
        sa.Column("intent_at", UtcDateTime(), nullable=True),
        sa.Column("confirmed_at", UtcDateTime(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_steps"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name="fk_steps_run_id_runs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="fk_steps_task_id_tasks", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_steps_idempotency_key"),
        sa.CheckConstraint("loop_index >= 0", name="ck_steps_loop_index_non_negative"),
        sa.CheckConstraint("attempt >= 0", name="ck_steps_attempt_non_negative"),
        sa.CheckConstraint("fence_token >= 0", name="ck_steps_fence_token_non_negative"),
    )
    op.create_index("ix_steps_task_loop", "steps", ["task_id", "loop_index"], unique=False)

    op.create_table(
        "checkpoints",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("run_id", sa.String(ID_LEN), nullable=False),
        sa.Column("task_id", sa.String(ID_LEN), nullable=True),
        sa.Column("loop_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(ENUM_LEN), nullable=False),
        sa.Column("snapshot", JsonType(), nullable=False),
        sa.Column("last_step_id", sa.String(ID_LEN), nullable=True),
        sa.Column("last_event_seq", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_checkpoints"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name="fk_checkpoints_run_id_runs", ondelete="CASCADE"
        ),
        sa.CheckConstraint("loop_index >= 0", name="ck_checkpoints_loop_index_non_negative"),
        sa.CheckConstraint("last_event_seq >= 0", name="ck_checkpoints_seq_non_negative"),
    )
    op.create_index("ix_checkpoints_created_at", "checkpoints", ["created_at"], unique=False)
    op.create_index(
        "ix_checkpoints_run_loop", "checkpoints", ["run_id", "loop_index"], unique=False
    )

    op.create_table(
        "idempotency_records",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(ENUM_LEN), nullable=False),
        sa.Column("step_id", sa.String(ID_LEN), nullable=True),
        sa.Column("external_ref", sa.String(500), nullable=True),
        sa.Column("response_snapshot", JsonType(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("key", name="pk_idempotency_records"),
    )
    op.create_index("ix_idem_expires", "idempotency_records", ["expires_at"], unique=False)


def downgrade() -> None:
    """按外键依赖的逆序删表。"""
    op.drop_index("ix_idem_expires", table_name="idempotency_records")
    op.drop_table("idempotency_records")

    op.drop_index("ix_checkpoints_run_loop", table_name="checkpoints")
    op.drop_index("ix_checkpoints_created_at", table_name="checkpoints")
    op.drop_table("checkpoints")

    op.drop_index("ix_steps_task_loop", table_name="steps")
    op.drop_table("steps")

    op.drop_index("ix_leases_worker", table_name="leases")
    op.drop_index("ix_leases_task", table_name="leases")
    op.drop_table("leases")

    op.drop_index("ix_tasks_run_state", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_runs_state", table_name="runs")
    op.drop_index("ix_runs_project_created", table_name="runs")
    op.drop_table("runs")

    op.drop_index("ix_repos_project_id", table_name="repos")
    op.drop_table("repos")

    op.drop_table("projects")
