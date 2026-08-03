"""memory_items / milestone_summaries / pinned_constraints / memory_lineage / inverted_index / index_stats

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-05

分层记忆的 6 张表。建表顺序被外键锁死：

``milestone_summaries`` → ``memory_items``（``compressed_into`` 指向摘要）
→ ``memory_lineage``（同时指向摘要与原始条目）。

``inverted_index`` 与 ``index_stats`` 用**复合主键**而不是代理键：
倒排索引的自然键就是 ``(term, doc_id)``，再加一个 ULID 只会白白多一个 B-Tree。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from loopforge.db.types import JsonType, UtcDateTime

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_LEN = 32
ID_LEN = 26


def upgrade() -> None:
    """建立记忆层相关表。"""
    op.create_table(
        "milestone_summaries",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("run_id", sa.String(ID_LEN), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("covers_from_seq", sa.BigInteger(), nullable=False),
        sa.Column("covers_to_seq", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("source_item_ids", JsonType(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_milestone_summaries"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name="fk_milestone_summaries_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("level >= 0", name="ck_ms_level_non_negative"),
        sa.CheckConstraint("tokens >= 0", name="ck_ms_tokens_non_negative"),
        sa.CheckConstraint("covers_to_seq >= covers_from_seq", name="ck_ms_seq_range_ordered"),
    )
    op.create_index("ix_ms_run_level", "milestone_summaries", ["run_id", "level"], unique=False)

    op.create_table(
        "memory_items",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("run_id", sa.String(ID_LEN), nullable=False),
        sa.Column("task_id", sa.String(ID_LEN), nullable=True),
        sa.Column("layer", sa.String(ENUM_LEN), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("salience", sa.Float(), nullable=False),
        sa.Column("compressed_into", sa.String(ID_LEN), nullable=True),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("meta", JsonType(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_memory_items"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name="fk_memory_items_run_id_runs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["compressed_into"],
            ["milestone_summaries.id"],
            name="fk_memory_items_compressed_into_milestone_summaries",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("tokens >= 0", name="ck_memory_items_tokens_non_negative"),
        sa.CheckConstraint(
            "salience >= 0.0 AND salience <= 1.0", name="ck_memory_items_salience_range"
        ),
    )
    op.create_index("ix_memory_run_layer", "memory_items", ["run_id", "layer"], unique=False)
    op.create_index("ix_memory_salience", "memory_items", ["run_id", "salience"], unique=False)

    op.create_table(
        "pinned_constraints",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("run_id", sa.String(ID_LEN), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(ENUM_LEN), nullable=False),
        sa.Column("origin_step_id", sa.String(ID_LEN), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pinned_constraints"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name="fk_pinned_constraints_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("run_id", "text_hash", name="uq_pinned_run_text_hash"),
        sa.CheckConstraint("priority >= 0", name="ck_pinned_priority_non_negative"),
        sa.CheckConstraint("hit_count >= 0", name="ck_pinned_hit_count_non_negative"),
    )
    op.create_index(
        "ix_pinned_run_active", "pinned_constraints", ["run_id", "priority"], unique=False
    )

    op.create_table(
        "memory_lineage",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("summary_id", sa.String(ID_LEN), nullable=False),
        sa.Column("source_item_id", sa.String(ID_LEN), nullable=False),
        sa.Column("info_loss_score", sa.Float(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_memory_lineage"),
        sa.ForeignKeyConstraint(
            ["summary_id"],
            ["milestone_summaries.id"],
            name="fk_memory_lineage_summary_id_milestone_summaries",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_item_id"],
            ["memory_items.id"],
            name="fk_memory_lineage_source_item_id_memory_items",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("summary_id", "source_item_id", name="uq_lineage_pair"),
        sa.CheckConstraint(
            "info_loss_score >= 0.0 AND info_loss_score <= 1.0", name="ck_lineage_loss_range"
        ),
    )
    op.create_index("ix_lineage_summary", "memory_lineage", ["summary_id"], unique=False)
    op.create_index("ix_lineage_source", "memory_lineage", ["source_item_id"], unique=False)

    op.create_table(
        "inverted_index",
        sa.Column("term", sa.String(200), nullable=False),
        sa.Column("doc_id", sa.String(ID_LEN), nullable=False),
        sa.Column("doc_type", sa.String(ENUM_LEN), nullable=False),
        sa.Column("run_id", sa.String(ID_LEN), nullable=False),
        sa.Column("tf", sa.Integer(), nullable=False),
        sa.Column("positions", JsonType(), nullable=False),
        sa.PrimaryKeyConstraint("term", "doc_id", name="pk_inverted_index"),
        sa.CheckConstraint("tf >= 0", name="ck_ii_tf_non_negative"),
    )
    op.create_index("ix_ii_run_term", "inverted_index", ["run_id", "term"], unique=False)

    op.create_table(
        "index_stats",
        sa.Column("run_id", sa.String(ID_LEN), nullable=False),
        sa.Column("doc_type", sa.String(ENUM_LEN), nullable=False),
        sa.Column("doc_count", sa.Integer(), nullable=False),
        sa.Column("avg_doc_len", sa.Float(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("run_id", "doc_type", name="pk_index_stats"),
        sa.CheckConstraint("doc_count >= 0", name="ck_index_stats_doc_count_non_negative"),
        sa.CheckConstraint("avg_doc_len >= 0", name="ck_index_stats_avg_len_non_negative"),
    )


def downgrade() -> None:
    """逆序删除。"""
    op.drop_table("index_stats")

    op.drop_index("ix_ii_run_term", table_name="inverted_index")
    op.drop_table("inverted_index")

    op.drop_index("ix_lineage_source", table_name="memory_lineage")
    op.drop_index("ix_lineage_summary", table_name="memory_lineage")
    op.drop_table("memory_lineage")

    op.drop_index("ix_pinned_run_active", table_name="pinned_constraints")
    op.drop_table("pinned_constraints")

    op.drop_index("ix_memory_salience", table_name="memory_items")
    op.drop_index("ix_memory_run_layer", table_name="memory_items")
    op.drop_table("memory_items")

    op.drop_index("ix_ms_run_level", table_name="milestone_summaries")
    op.drop_table("milestone_summaries")
