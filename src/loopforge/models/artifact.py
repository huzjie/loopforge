"""Artifact（产物）ORM 模型。

对应表：``artifacts``（``docs/ARCHITECTURE.md`` §5.1）。

产物本体存在对象存储 / 本地目录里，表中只留 ``storage_uri`` + ``sha256``，
避免把大 blob 塞进关系库。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from loopforge.constants import ID_COLUMN_LENGTH
from loopforge.core.ids import new_ulid
from loopforge.db.base import Base
from loopforge.db.mixins import utcnow
from loopforge.db.types import JsonType, UtcDateTime

__all__ = ["Artifact"]


class Artifact(Base):
    """一件产物：日志、报告、diff、覆盖率文件、截图……"""

    __tablename__ = "artifacts"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_artifacts_size_non_negative"),
        UniqueConstraint("run_id", "sha256", name="uq_artifacts_sha_run"),
        Index("ix_artifacts_run", "run_id"),
        Index("ix_artifacts_kind", "kind"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    run_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    step_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="log / diff / report / coverage / screenshot / other"
    )
    storage_uri: Mapped[str] = mapped_column(
        String(1000), nullable=False, doc="file:// 或 s3:// 等统一资源标识"
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    mime_type: Mapped[str] = mapped_column(
        String(200), nullable=False, default="application/octet-stream"
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
