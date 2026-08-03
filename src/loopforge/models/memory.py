"""上下文记忆相关 ORM 模型（WP5 使用，WP1 负责建表）。

对应表：``memory_items``、``milestone_summaries``、``pinned_constraints``、
``memory_lineage``、``inverted_index``、``index_stats``
（``docs/ARCHITECTURE.md`` §5.1）。

.. warning::
   ``memory_items`` **永不物理删除**。压缩只是把条目标记 ``compressed_into``
   指向某条 milestone summary；原文必须留存，否则保真度校验与审计无从谈起。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from loopforge.constants import ID_COLUMN_LENGTH
from loopforge.core.enums import MemoryLayer
from loopforge.core.ids import new_ulid
from loopforge.db.base import Base
from loopforge.db.mixins import utcnow
from loopforge.db.types import JsonType, StrEnumType, UtcDateTime

__all__ = [
    "MemoryItem",
    "MilestoneSummary",
    "PinnedConstraint",
    "MemoryLineage",
    "InvertedIndex",
    "IndexStats",
]

#: 仅索引「未被压缩」的活跃记忆，缩小热点索引体积。
_UNCOMPRESSED_PREDICATE: str = "compressed_into IS NULL"

#: 仅索引生效中的钉固约束。SQLite 布尔存 0/1，PostgreSQL 是真布尔，故谓词分方言。
_ACTIVE_PINNED_PREDICATE: str = "active = 1"
_ACTIVE_PINNED_PREDICATE_PG: str = "active"


class MemoryItem(Base):
    """一条记忆条目（五层记忆的原子单位）。"""

    __tablename__ = "memory_items"
    __table_args__ = (
        CheckConstraint("tokens >= 0", name="ck_memory_items_tokens_non_negative"),
        CheckConstraint(
            "salience >= 0.0 AND salience <= 1.0", name="ck_memory_items_salience_range"
        ),
        Index("ix_memory_run_layer", "run_id", "layer"),
        Index("ix_memory_salience", "run_id", "salience"),
        Index(
            "ix_memory_compressed",
            "run_id",
            "layer",
            sqlite_where=text(_UNCOMPRESSED_PREDICATE),
            postgresql_where=text(_UNCOMPRESSED_PREDICATE),
        ),
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
    layer: Mapped[MemoryLayer] = mapped_column(
        StrEnumType(MemoryLayer), nullable=False, default=MemoryLayer.WORKING
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    salience: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5, doc="显著性 [0,1]，决定压缩优先级"
    )
    compressed_into: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("milestone_summaries.id", ondelete="SET NULL"),
        nullable=True,
        doc="非空表示已被压缩进某条里程碑摘要（原文仍保留）",
    )
    embedding: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True, doc="float32 向量的原始字节；无向量后端时为 NULL"
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    @property
    def is_compressed(self) -> bool:
        """是否已被压缩进里程碑摘要。"""
        return self.compressed_into is not None


class MilestoneSummary(Base):
    """里程碑摘要 —— 对一段事件区间的分级压缩产物。"""

    __tablename__ = "milestone_summaries"
    __table_args__ = (
        CheckConstraint("level >= 0", name="ck_ms_level_non_negative"),
        CheckConstraint("tokens >= 0", name="ck_ms_tokens_non_negative"),
        CheckConstraint(
            "covers_to_seq >= covers_from_seq", name="ck_ms_seq_range_ordered"
        ),
        Index("ix_ms_run_level", "run_id", "level"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    run_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, doc="0=原始摘要，1=摘要的摘要，依此递归"
    )
    covers_from_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    covers_to_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_item_ids: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class PinnedConstraint(Base):
    """钉固约束 —— 永不被压缩掉的硬性要求。"""

    __tablename__ = "pinned_constraints"
    __table_args__ = (
        CheckConstraint("priority >= 0", name="ck_pinned_priority_non_negative"),
        CheckConstraint("hit_count >= 0", name="ck_pinned_hit_count_non_negative"),
        UniqueConstraint("run_id", "text_hash", name="uq_pinned_run_text_hash"),
        Index(
            "ix_pinned_run_active",
            "run_id",
            "priority",
            sqlite_where=text(_ACTIVE_PINNED_PREDICATE),
            postgresql_where=text(_ACTIVE_PINNED_PREDICATE_PG),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    run_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    text_content: Mapped[str] = mapped_column(
        "text", Text, nullable=False, doc="约束原文（列名为 text，属性名避开内建）"
    )
    text_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", doc="text 的 sha256，用于 Run 内去重"
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    origin: Mapped[str] = mapped_column(
        String(32), nullable=False, default="user", doc="user / planner / policy / system"
    )
    origin_step_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH), nullable=True
    )
    hit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, doc="被注入上下文的次数"
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class MemoryLineage(Base):
    """压缩谱系 —— 记录「哪条原文被压进了哪条摘要，损失多少」。"""

    __tablename__ = "memory_lineage"
    __table_args__ = (
        CheckConstraint(
            "info_loss_score >= 0.0 AND info_loss_score <= 1.0",
            name="ck_lineage_loss_range",
        ),
        UniqueConstraint("summary_id", "source_item_id", name="uq_lineage_pair"),
        Index("ix_lineage_summary", "summary_id"),
        Index("ix_lineage_source", "source_item_id"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    summary_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("milestone_summaries.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_item_id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("memory_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    info_loss_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, doc="信息损失评分 [0,1]，越大越危险"
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class InvertedIndex(Base):
    """BM25 倒排表（SQLite / PostgreSQL 通用，不依赖任何全文扩展）。"""

    __tablename__ = "inverted_index"
    __table_args__ = (
        CheckConstraint("tf >= 0", name="ck_ii_tf_non_negative"),
        Index("ix_ii_run_term", "run_id", "term"),
    )

    term: Mapped[str] = mapped_column(String(200), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True)
    doc_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="memory_item", doc="memory_item / summary / …"
    )
    run_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), nullable=False)
    tf: Mapped[int] = mapped_column(Integer, nullable=False, default=0, doc="词频")
    positions: Mapped[list[int]] = mapped_column(
        JsonType, nullable=False, default=list, doc="词在文档中的位置，用于短语匹配"
    )


class IndexStats(Base):
    """BM25 归一化参数（每个 run × doc_type 一行）。"""

    __tablename__ = "index_stats"
    __table_args__ = (
        CheckConstraint("doc_count >= 0", name="ck_index_stats_doc_count_non_negative"),
        CheckConstraint("avg_doc_len >= 0", name="ck_index_stats_avg_len_non_negative"),
    )

    run_id: Mapped[str] = mapped_column(String(ID_COLUMN_LENGTH), primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_doc_len: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
