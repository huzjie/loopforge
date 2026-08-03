"""EvolutionItem / WebhookDelivery ORM 模型。

对应表：``evolution_items``、``webhook_deliveries``（``docs/ARCHITECTURE.md`` §5.1）。

* **evolution_items**：自进化候选项 —— 从失败日志、评审意见、外部反馈里
  抽取的改进点，去重聚类后择优晋升为正式 Task。
* **webhook_deliveries**：外部 Webhook 投递记录，``delivery_id`` 唯一
  → 天然幂等，重复投递直接短路。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from loopforge.constants import ID_COLUMN_LENGTH
from loopforge.core.ids import new_ulid
from loopforge.db.base import Base
from loopforge.db.mixins import utcnow
from loopforge.db.types import JsonType, UtcDateTime

__all__ = ["EvolutionItem", "WebhookDelivery", "EVOLUTION_STATES"]

#: 自进化候选项状态取值。
EVOLUTION_STATES: tuple[str, ...] = (
    "new",
    "clustered",
    "scored",
    "promoted",
    "rejected",
    "duplicate",
)


class EvolutionItem(Base):
    """一条自进化候选项。"""

    __tablename__ = "evolution_items"
    __table_args__ = (
        CheckConstraint("score >= 0.0 AND score <= 1.0", name="ck_evo_score_range"),
        Index("ix_evo_state_score", "state", "score"),
        Index("ix_evo_cluster", "cluster_id"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    run_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, default="review", doc="review / failure / user / metric"
    )
    raw: Mapped[str] = mapped_column(Text, nullable=False, default="", doc="原始文本")
    normalized: Mapped[str] = mapped_column(
        Text, nullable=False, default="", doc="归一化后的文本，用于聚类"
    )
    cluster_id: Mapped[str | None] = mapped_column(String(ID_COLUMN_LENGTH), nullable=True)
    simhash: Mapped[str] = mapped_column(
        String(16), nullable=False, default="", doc="64 位 SimHash 的 hex，用于近重复检测"
    )
    score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, doc="价值评分 [0,1]"
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    promoted_task_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH), nullable=True
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class WebhookDelivery(Base):
    """一次 Webhook 投递记录（``delivery_id`` 唯一保证幂等）。"""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("provider", "delivery_id", name="uq_webhook_delivery_id"),
        Index("ix_webhook_processed", "processed", "received_at"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="github")
    delivery_id: Mapped[str] = mapped_column(
        String(200), nullable=False, doc="外部投递唯一 ID（GitHub 的 X-GitHub-Delivery）"
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="已脱敏的投递体"
    )
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow
    )
    processed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
