"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

注意：LoopForge 的迁移链禁止 ``--autogenerate``，所有 DDL 必须手写并在
SQLite 与 PostgreSQL 上双跑通过。新增迁移后请运行 ``make check-heads``
确认仍然只有单一 head。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """向前迁移。"""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """向后回滚。"""
    ${downgrades if downgrades else "pass"}
