"""workers / provider_configs / agent_roles / users / api_keys / evolution_items / webhook_deliveries

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-05

系统面的 7 张表。凡是存密钥的列一律以 ``_enc`` 结尾（``api_key_enc``、
``webhook_secret_enc``），列类型为 ``TEXT`` 而不是 ``VARCHAR(n)`` —— 
加密后的密文长度取决于算法与轮换策略，写死长度迟早会截断。

``api_keys`` 只存 ``hashed_secret`` 与可公开的 ``prefix``；数据库里**永远不存明文密钥**，
即使整库泄漏，攻击者也拿不到可用凭据。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from loopforge.db.types import JsonType, UtcDateTime

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_LEN = 32
ID_LEN = 26


def upgrade() -> None:
    """建立系统面表。"""
    op.create_table(
        "workers",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(ENUM_LEN), nullable=False),
        sa.Column("capabilities", JsonType(), nullable=False),
        sa.Column("state", sa.String(ENUM_LEN), nullable=False),
        sa.Column("current_task_id", sa.String(ID_LEN), nullable=True),
        sa.Column("drain", sa.Boolean(), nullable=False),
        sa.Column("started_at", UtcDateTime(), nullable=False),
        sa.Column("last_heartbeat_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workers"),
        sa.CheckConstraint("pid >= 0", name="ck_workers_pid_non_negative"),
    )

    op.create_table(
        "provider_configs",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(ENUM_LEN), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("default_model", sa.String(200), nullable=True),
        sa.Column("options", JsonType(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("failover_to", sa.String(64), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_provider_configs"),
        sa.UniqueConstraint("name", name="uq_provider_name"),
        sa.CheckConstraint("priority >= 0", name="ck_provider_configs_priority_non_negative"),
    )
    op.create_index(
        "ix_provider_enabled_priority",
        "provider_configs",
        ["enabled", "priority"],
        unique=False,
    )

    op.create_table(
        "agent_roles",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("name", sa.String(ENUM_LEN), nullable=False),
        sa.Column("provider_config_id", sa.String(ID_LEN), nullable=True),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("allowed_tools", JsonType(), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_roles"),
        sa.ForeignKeyConstraint(
            ["provider_config_id"],
            ["provider_configs.id"],
            name="fk_agent_roles_provider_config_id_provider_configs",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("name", name="uq_agent_roles_name"),
        sa.CheckConstraint(
            "temperature >= 0.0 AND temperature <= 2.0", name="ck_agent_roles_temperature_range"
        ),
        sa.CheckConstraint("max_tokens > 0", name="ck_agent_roles_max_tokens_positive"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("role", sa.String(ENUM_LEN), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("prefix", sa.String(ENUM_LEN), nullable=False),
        sa.Column("hashed_secret", sa.String(255), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("scopes", JsonType(), nullable=False),
        sa.Column("user_id", sa.String(ID_LEN), nullable=True),
        sa.Column("expires_at", UtcDateTime(), nullable=True),
        sa.Column("last_used_at", UtcDateTime(), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_api_keys_user_id_users", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("prefix", name="uq_api_keys_prefix"),
    )
    op.create_index("ix_api_keys_user", "api_keys", ["user_id"], unique=False)

    op.create_table(
        "evolution_items",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("run_id", sa.String(ID_LEN), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("raw", sa.Text(), nullable=False),
        sa.Column("normalized", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.String(ID_LEN), nullable=True),
        sa.Column("simhash", sa.String(16), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("state", sa.String(ENUM_LEN), nullable=False),
        sa.Column("promoted_task_id", sa.String(ID_LEN), nullable=True),
        sa.Column("meta", JsonType(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evolution_items"),
        sa.CheckConstraint("score >= 0.0 AND score <= 1.0", name="ck_evo_score_range"),
    )
    op.create_index("ix_evo_cluster", "evolution_items", ["cluster_id"], unique=False)

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("provider", sa.String(ENUM_LEN), nullable=False),
        sa.Column("delivery_id", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", JsonType(), nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("received_at", UtcDateTime(), nullable=False),
        sa.Column("processed_at", UtcDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_deliveries"),
        sa.UniqueConstraint("provider", "delivery_id", name="uq_webhook_delivery_id"),
    )


def downgrade() -> None:
    """逆序删除。"""
    op.drop_table("webhook_deliveries")

    op.drop_index("ix_evo_cluster", table_name="evolution_items")
    op.drop_table("evolution_items")

    op.drop_index("ix_api_keys_user", table_name="api_keys")
    op.drop_table("api_keys")

    op.drop_table("users")
    op.drop_table("agent_roles")

    op.drop_index("ix_provider_enabled_priority", table_name="provider_configs")
    op.drop_table("provider_configs")

    op.drop_table("workers")
