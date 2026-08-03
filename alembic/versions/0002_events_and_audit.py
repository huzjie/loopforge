"""events / audit_logs / llm_calls / tool_calls / artifacts

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-05

可观测与取证面的 5 张表。两条设计上的硬约束在这里落地：

1. ``events.seq`` 是**自增整数主键**而不是 ULID —— 事件流需要一个全局单调序号
   来做 WebSocket 回补（``since_seq``）与哈希链校验，ULID 的字典序不足以承担这个职责。
   ``events.id`` 仍保留 ULID 并加唯一约束，供外部引用。
2. ``events.hash`` 唯一 —— 哈希链一旦出现重复摘要，就意味着有人重放或篡改了事件，
   这里用 DB 约束而不是应用层校验来兜底。

三张表都是 **append-only**：没有 ``updated_at``，也不允许 UPDATE / DELETE
（由仓储层的 ``AppendOnlyRepository`` 保证，DB 层只提供结构）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from loopforge.db.types import DecimalMoney, JsonType, UtcDateTime

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_LEN = 32
ID_LEN = 26


def upgrade() -> None:
    """建立事件、审计与调用记录表。"""
    op.create_table(
        "events",
        sa.Column("seq", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("run_id", sa.String(ID_LEN), nullable=True),
        sa.Column("task_id", sa.String(ID_LEN), nullable=True),
        sa.Column("step_id", sa.String(ID_LEN), nullable=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(ENUM_LEN), nullable=False),
        sa.Column("payload", JsonType(), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("prev_hash", sa.String(64), nullable=True),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("seq", name="pk_events"),
        sa.UniqueConstraint("id", name="uq_events_id"),
        sa.UniqueConstraint("hash", name="uq_events_hash"),
    )
    op.create_index("ix_events_type", "events", ["type"], unique=False)
    op.create_index("ix_events_trace", "events", ["trace_id"], unique=False)
    op.create_index("ix_events_created", "events", ["created_at"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("actor_type", sa.String(ENUM_LEN), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(ID_LEN), nullable=True),
        sa.Column("before", JsonType(), nullable=False),
        sa.Column("after", JsonType(), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index(
        "ix_audit_resource", "audit_logs", ["resource_type", "resource_id"], unique=False
    )
    op.create_index("ix_audit_actor_created", "audit_logs", ["actor", "created_at"], unique=False)

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("step_id", sa.String(ID_LEN), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("role", sa.String(ENUM_LEN), nullable=True),
        sa.Column("request", JsonType(), nullable=False),
        sa.Column("response", JsonType(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", DecimalMoney(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("failover_from", sa.String(64), nullable=True),
        sa.Column("finish_reason", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(ENUM_LEN), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_llm_calls"),
        sa.ForeignKeyConstraint(
            ["step_id"], ["steps.id"], name="fk_llm_calls_step_id_steps", ondelete="SET NULL"
        ),
        sa.CheckConstraint("prompt_tokens >= 0", name="ck_llm_calls_prompt_tokens_non_negative"),
        sa.CheckConstraint(
            "completion_tokens >= 0", name="ck_llm_calls_completion_tokens_non_negative"
        ),
        sa.CheckConstraint("cached_tokens >= 0", name="ck_llm_calls_cached_tokens_non_negative"),
        sa.CheckConstraint("cost_usd >= 0", name="ck_llm_calls_cost_non_negative"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_llm_calls_latency_non_negative"),
    )
    op.create_index("ix_llm_calls_step", "llm_calls", ["step_id"], unique=False)
    op.create_index(
        "ix_llm_calls_model_created", "llm_calls", ["model", "created_at"], unique=False
    )

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("step_id", sa.String(ID_LEN), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("args", JsonType(), nullable=False),
        sa.Column("result", JsonType(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("policy_decision", sa.String(ENUM_LEN), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("bytes_out", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(ENUM_LEN), nullable=True),
        sa.Column("stderr_excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tool_calls"),
        sa.ForeignKeyConstraint(
            ["step_id"], ["steps.id"], name="fk_tool_calls_step_id_steps", ondelete="SET NULL"
        ),
        sa.CheckConstraint("duration_ms >= 0", name="ck_tool_calls_duration_non_negative"),
        sa.CheckConstraint("bytes_out >= 0", name="ck_tool_calls_bytes_out_non_negative"),
    )
    op.create_index("ix_tool_calls_step", "tool_calls", ["step_id"], unique=False)
    op.create_index("ix_tool_calls_name", "tool_calls", ["tool_name"], unique=False)

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("run_id", sa.String(ID_LEN), nullable=False),
        sa.Column("task_id", sa.String(ID_LEN), nullable=True),
        sa.Column("step_id", sa.String(ID_LEN), nullable=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("storage_uri", sa.String(1000), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(200), nullable=False),
        sa.Column("meta", JsonType(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_artifacts"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name="fk_artifacts_run_id_runs", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("run_id", "sha256", name="uq_artifacts_sha_run"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_artifacts_size_non_negative"),
    )
    op.create_index("ix_artifacts_run", "artifacts", ["run_id"], unique=False)
    op.create_index("ix_artifacts_kind", "artifacts", ["kind"], unique=False)


def downgrade() -> None:
    """逆序删除。"""
    op.drop_index("ix_artifacts_kind", table_name="artifacts")
    op.drop_index("ix_artifacts_run", table_name="artifacts")
    op.drop_table("artifacts")

    op.drop_index("ix_tool_calls_name", table_name="tool_calls")
    op.drop_index("ix_tool_calls_step", table_name="tool_calls")
    op.drop_table("tool_calls")

    op.drop_index("ix_llm_calls_model_created", table_name="llm_calls")
    op.drop_index("ix_llm_calls_step", table_name="llm_calls")
    op.drop_table("llm_calls")

    op.drop_index("ix_audit_actor_created", table_name="audit_logs")
    op.drop_index("ix_audit_resource", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_events_created", table_name="events")
    op.drop_index("ix_events_trace", table_name="events")
    op.drop_index("ix_events_type", table_name="events")
    op.drop_table("events")
