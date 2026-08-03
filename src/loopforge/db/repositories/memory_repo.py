"""记忆层仓储（WP5 消费，WP1 提供）。

.. warning::
   :class:`MemoryRepository` **不提供任何删除方法**。压缩只是把
   ``compressed_into`` 指向摘要，原文永久保留 —— 这是保真度校验与审计的前提。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update

from loopforge.core.clock import get_clock
from loopforge.core.enums import MemoryLayer
from loopforge.core.hashing import sha256_hex
from loopforge.core.ids import new_ulid
from loopforge.db.repositories.base import BaseRepository, clamp_limit
from loopforge.models.memory import (
    IndexStats,
    InvertedIndex,
    MemoryItem,
    MemoryLineage,
    MilestoneSummary,
    PinnedConstraint,
)

__all__ = [
    "MemoryRepository",
    "MilestoneSummaryRepository",
    "PinnedConstraintRepository",
    "InvertedIndexRepository",
]


class MemoryRepository(BaseRepository[MemoryItem]):
    """记忆条目仓储。"""

    model = MemoryItem

    async def add_item(
        self,
        *,
        run_id: str,
        content: str,
        layer: MemoryLayer = MemoryLayer.WORKING,
        task_id: str | None = None,
        tokens: int = 0,
        salience: float = 0.5,
        meta: dict[str, object] | None = None,
        embedding: bytes | None = None,
        now: datetime | None = None,
    ) -> MemoryItem:
        """新增一条记忆。

        :param salience: 显著性，自动夹到 ``[0, 1]``。
        """
        moment = now if now is not None else get_clock().now()
        item = MemoryItem(
            id=new_ulid(),
            run_id=run_id,
            task_id=task_id,
            layer=layer,
            content=content,
            tokens=max(0, tokens),
            salience=min(1.0, max(0.0, salience)),
            embedding=embedding,
            meta=dict(meta) if meta else {},
            created_at=moment,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_by_layer(
        self,
        run_id: str,
        layer: MemoryLayer,
        *,
        include_compressed: bool = False,
        limit: int | None = None,
    ) -> list[MemoryItem]:
        """按层取记忆，按显著性倒序（命中 ``ix_memory_run_layer``）。"""
        stmt = select(MemoryItem).where(
            MemoryItem.run_id == run_id, MemoryItem.layer == layer
        )
        if not include_compressed:
            stmt = stmt.where(MemoryItem.compressed_into.is_(None))
        stmt = (
            stmt.order_by(MemoryItem.salience.desc(), MemoryItem.created_at.desc())
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_compressed(self, item_ids: Sequence[str], summary_id: str) -> int:
        """把一批记忆标记为「已压缩进某摘要」。**不删除原文。**

        :returns: 受影响行数。
        """
        if not item_ids:
            return 0
        result = await self.session.execute(
            update(MemoryItem)
            .where(MemoryItem.id.in_(list(item_ids)))
            .values(compressed_into=summary_id)
        )
        return int(result.rowcount or 0)

    async def total_tokens(
        self, run_id: str, *, layer: MemoryLayer | None = None
    ) -> int:
        """统计未压缩记忆的 token 总量（上下文预算核算依据）。"""
        stmt = (
            select(func.coalesce(func.sum(MemoryItem.tokens), 0))
            .select_from(MemoryItem)
            .where(MemoryItem.run_id == run_id, MemoryItem.compressed_into.is_(None))
        )
        if layer is not None:
            stmt = stmt.where(MemoryItem.layer == layer)
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def add_lineage(
        self,
        *,
        summary_id: str,
        source_item_id: str,
        info_loss_score: float = 0.0,
        now: datetime | None = None,
    ) -> MemoryLineage:
        """登记一条压缩谱系。"""
        moment = now if now is not None else get_clock().now()
        lineage = MemoryLineage(
            id=new_ulid(),
            summary_id=summary_id,
            source_item_id=source_item_id,
            info_loss_score=min(1.0, max(0.0, info_loss_score)),
            created_at=moment,
        )
        self.session.add(lineage)
        await self.session.flush()
        return lineage

    async def lineage_for_summary(
        self, summary_id: str, *, limit: int | None = None
    ) -> list[MemoryLineage]:
        """取某摘要的全部谱系记录。"""
        stmt = (
            select(MemoryLineage)
            .where(MemoryLineage.summary_id == summary_id)
            .order_by(MemoryLineage.created_at)
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class MilestoneSummaryRepository(BaseRepository[MilestoneSummary]):
    """里程碑摘要仓储。"""

    model = MilestoneSummary

    async def list_by_level(
        self, run_id: str, level: int, *, limit: int | None = None
    ) -> list[MilestoneSummary]:
        """按层级取摘要（命中 ``ix_ms_run_level``）。"""
        stmt = (
            select(MilestoneSummary)
            .where(MilestoneSummary.run_id == run_id, MilestoneSummary.level == level)
            .order_by(MilestoneSummary.covers_from_seq)
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def max_covered_seq(self, run_id: str) -> int:
        """已被摘要覆盖到的最大事件 seq（下一次压缩的起点）。"""
        result = await self.session.execute(
            select(func.coalesce(func.max(MilestoneSummary.covers_to_seq), 0)).where(
                MilestoneSummary.run_id == run_id
            )
        )
        return int(result.scalar_one() or 0)


class PinnedConstraintRepository(BaseRepository[PinnedConstraint]):
    """钉固约束仓储。"""

    model = PinnedConstraint

    async def pin(
        self,
        *,
        run_id: str,
        text: str,
        priority: int = 100,
        origin: str = "user",
        origin_step_id: str | None = None,
        now: datetime | None = None,
    ) -> PinnedConstraint:
        """钉固一条约束；同 Run 内文本重复时返回已有记录（不报错）。"""
        digest = sha256_hex(text)
        existing = await self.session.execute(
            select(PinnedConstraint).where(
                PinnedConstraint.run_id == run_id, PinnedConstraint.text_hash == digest
            )
        )
        found = existing.scalar_one_or_none()
        if found is not None:
            return found
        moment = now if now is not None else get_clock().now()
        constraint = PinnedConstraint(
            id=new_ulid(),
            run_id=run_id,
            text_content=text,
            text_hash=digest,
            priority=priority,
            origin=origin,
            origin_step_id=origin_step_id,
            active=True,
            created_at=moment,
        )
        self.session.add(constraint)
        await self.session.flush()
        return constraint

    async def list_active(self, run_id: str, *, limit: int | None = None) -> list[PinnedConstraint]:
        """列出生效中的约束，按优先级倒序（命中 ``ix_pinned_run_active``）。"""
        stmt = (
            select(PinnedConstraint)
            .where(PinnedConstraint.run_id == run_id, PinnedConstraint.active.is_(True))
            .order_by(PinnedConstraint.priority.desc(), PinnedConstraint.created_at)
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def record_hit(self, constraint_id: str) -> bool:
        """命中计数 +1（统计哪些约束真正被注入过上下文）。"""
        result = await self.session.execute(
            update(PinnedConstraint)
            .where(PinnedConstraint.id == constraint_id)
            .values(hit_count=PinnedConstraint.hit_count + 1)
        )
        return int(result.rowcount or 0) == 1

    async def deactivate(self, constraint_id: str) -> bool:
        """停用一条约束（软下线，不删除）。"""
        result = await self.session.execute(
            update(PinnedConstraint)
            .where(PinnedConstraint.id == constraint_id)
            .values(active=False)
        )
        return int(result.rowcount or 0) == 1


class InvertedIndexRepository(BaseRepository[InvertedIndex]):
    """BM25 倒排索引仓储（复合主键，不走 BaseRepository 的单主键方法）。"""

    model = InvertedIndex
    pk_field = "term"

    async def upsert_posting(
        self,
        *,
        term: str,
        doc_id: str,
        run_id: str,
        tf: int,
        positions: Sequence[int] | None = None,
        doc_type: str = "memory_item",
    ) -> InvertedIndex:
        """写入或更新一条倒排记录。"""
        existing = await self.session.execute(
            select(InvertedIndex).where(
                InvertedIndex.term == term, InvertedIndex.doc_id == doc_id
            )
        )
        posting = existing.scalar_one_or_none()
        if posting is not None:
            posting.tf = max(0, tf)
            posting.positions = list(positions or [])
            posting.run_id = run_id
            posting.doc_type = doc_type
            await self.session.flush()
            return posting
        posting = InvertedIndex(
            term=term,
            doc_id=doc_id,
            run_id=run_id,
            doc_type=doc_type,
            tf=max(0, tf),
            positions=list(positions or []),
        )
        self.session.add(posting)
        await self.session.flush()
        return posting

    async def postings_for_terms(
        self, run_id: str, terms: Sequence[str], *, limit: int | None = None
    ) -> list[InvertedIndex]:
        """按词项批量取倒排（命中 ``ix_ii_run_term``）。"""
        if not terms:
            return []
        stmt = (
            select(InvertedIndex)
            .where(InvertedIndex.run_id == run_id, InvertedIndex.term.in_(list(terms)))
            .order_by(InvertedIndex.term, InvertedIndex.tf.desc())
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def upsert_stats(
        self,
        *,
        run_id: str,
        doc_type: str,
        doc_count: int,
        avg_doc_len: float,
        now: datetime | None = None,
    ) -> IndexStats:
        """写入或更新 BM25 归一化参数。"""
        moment = now if now is not None else get_clock().now()
        existing = await self.session.execute(
            select(IndexStats).where(
                IndexStats.run_id == run_id, IndexStats.doc_type == doc_type
            )
        )
        stats = existing.scalar_one_or_none()
        if stats is not None:
            stats.doc_count = max(0, doc_count)
            stats.avg_doc_len = max(0.0, avg_doc_len)
            stats.updated_at = moment
            await self.session.flush()
            return stats
        stats = IndexStats(
            run_id=run_id,
            doc_type=doc_type,
            doc_count=max(0, doc_count),
            avg_doc_len=max(0.0, avg_doc_len),
            updated_at=moment,
        )
        self.session.add(stats)
        await self.session.flush()
        return stats

    async def get_stats(self, run_id: str, doc_type: str) -> IndexStats | None:
        """取 BM25 归一化参数。"""
        result = await self.session.execute(
            select(IndexStats).where(
                IndexStats.run_id == run_id, IndexStats.doc_type == doc_type
            )
        )
        return result.scalar_one_or_none()
