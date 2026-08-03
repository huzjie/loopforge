"""budgets / cost_records / policies / policy_rules / approvals

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-05

治理面的 5 张表。三处值得说明的取舍：

1. ``policy_rules.order`` 是 SQL 保留字，必须始终用双引号包裹（见检查约束里的
   ``"order" >= 0``）。ORM 侧把属性名映射为 ``order_index``，列名仍是 ``order``，
   因为策略 YAML 的字段名就叫 ``order``，改名会让配置与库表对不上。
2. 金额列一律 ``NUMERIC(18, 8)``（:class:`~loopforge.db.types.DecimalMoney`）。
   八位小数是为了容纳「每千 token 千分之几美分」这种量级，累加十万次也不漂。
3. ``budgets`` 的三档阈值用 CHECK 约束强制 ``warn <= throttle <= halt <= 1.0``，
   杜绝「限流阈值比停机阈值还高」这种配错了就永远不限流的静默故障。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from loopforge.db.types import DecimalMoney, JsonType, UtcDateTime

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_LEN = 32
ID_LEN = 26


def upgrade() -> None:
    """建立预算、成本、策略与审批表。"""
    op.create_table(
        "budgets",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("scope", sa.String(ENUM_LEN), nullable=False),
        sa.Column("scope_ref", sa.String(ID_LEN), nullable=False),
        sa.Column("limit_usd", DecimalMoney(), nullable=False),
        sa.Column("window", sa.String(ENUM_LEN), nullable=False),
        sa.Column("warn_at", sa.Float(), nullable=False),
        sa.Column("throttle_at", sa.Float(), nullable=False),
        sa.Column("halt_at", sa.Float(), nullable=False),
        sa.Column("consumed_usd", DecimalMoney(), nullable=False),
        sa.Column("window_start", UtcDateTime(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_budgets"),
        sa.UniqueConstraint("scope", "scope_ref", "window", name="uq_budgets_scope_ref_window"),
        sa.CheckConstraint("limit_usd >= 0", name="ck_budgets_limit_non_negative"),
        sa.CheckConstraint("consumed_usd >= 0", name="ck_budgets_consumed_non_negative"),
        sa.CheckConstraint("warn_at >= 0.0", name="ck_budgets_warn_non_negative"),
        sa.CheckConstraint(
            "warn_at <= throttle_at AND throttle_at <= halt_at AND halt_at <= 1.0",
            name="ck_budgets_threshold_order",
        ),
    )
    op.create_index("ix_budgets_active", "budgets", ["scope", "window_start"], unique=False)

    op.create_table(
        "cost_records",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("run_id", sa.String(ID_LEN), nullable=False),
        sa.Column("task_id", sa.String(ID_LEN), nullable=True),
        sa.Column("pr_ref", sa.String(200), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", DecimalMoney(), nullable=False),
        sa.Column("llm_call_id", sa.String(ID_LEN), nullable=True),
        sa.Column("budget_id", sa.String(ID_LEN), nullable=True),
        sa.Column("occurred_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_cost_records"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name="fk_cost_records_run_id_runs", ondelete="CASCADE"
        ),
        sa.CheckConstraint("prompt_tokens >= 0", name="ck_cost_records_prompt_non_negative"),
        sa.CheckConstraint(
            "completion_tokens >= 0", name="ck_cost_records_completion_non_negative"
        ),
        sa.CheckConstraint("cost_usd >= 0", name="ck_cost_records_cost_non_negative"),
    )
    op.create_index("ix_cost_task", "cost_records", ["task_id"], unique=False)
    op.create_index("ix_cost_model_time", "cost_records", ["model", "occurred_at"], unique=False)

    op.create_table(
        "policies",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("yaml_source", sa.Text(), nullable=False),
        sa.Column("compiled", JsonType(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("activated_at", UtcDateTime(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_policies"),
        sa.UniqueConstraint("name", "version", name="uq_policies_name_version"),
        sa.CheckConstraint("version > 0", name="ck_policies_version_positive"),
    )
    op.create_index("ix_policies_active", "policies", ["name"], unique=False)

    op.create_table(
        "policy_rules",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("policy_id", sa.String(ID_LEN), nullable=False),
        sa.Column("rule_id", sa.String(100), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("match", JsonType(), nullable=False),
        sa.Column("effect", sa.String(ENUM_LEN), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(ENUM_LEN), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_policy_rules"),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["policies.id"],
            name="fk_policy_rules_policy_id_policies",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("policy_id", "rule_id", name="uq_policy_rules_policy_rule"),
        sa.CheckConstraint('"order" >= 0', name="ck_policy_rules_order_non_negative"),
    )
    op.create_index("ix_rules_policy_order", "policy_rules", ["policy_id", "order"], unique=False)

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(ID_LEN), nullable=False),
        sa.Column("run_id", sa.String(ID_LEN), nullable=False),
        sa.Column("task_id", sa.String(ID_LEN), nullable=True),
        sa.Column("step_id", sa.String(ID_LEN), nullable=True),
        sa.Column("action_kind", sa.String(64), nullable=False),
        sa.Column("action_payload", JsonType(), nullable=False),
        sa.Column("risk_note", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.String(100), nullable=True),
        sa.Column("state", sa.String(ENUM_LEN), nullable=False),
        sa.Column("decided_by", sa.String(200), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("timeout_action", sa.String(ENUM_LEN), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=True),
        sa.Column("decided_at", UtcDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_approvals"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name="fk_approvals_run_id_runs", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_approvals_run", "approvals", ["run_id"], unique=False)


def downgrade() -> None:
    """逆序删除。"""
    op.drop_index("ix_approvals_run", table_name="approvals")
    op.drop_table("approvals")

    op.drop_index("ix_rules_policy_order", table_name="policy_rules")
    op.drop_table("policy_rules")

    op.drop_index("ix_policies_active", table_name="policies")
    op.drop_table("policies")

    op.drop_index("ix_cost_model_time", table_name="cost_records")
    op.drop_index("ix_cost_task", table_name="cost_records")
    op.drop_table("cost_records")

    op.drop_index("ix_budgets_active", table_name="budgets")
    op.drop_table("budgets")
