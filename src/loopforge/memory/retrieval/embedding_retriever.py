"""向量检索器 —— 默认关闭，不可用时优雅降级到 BM25。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.1、§8.2.4。

设计要点：

* **默认关闭**：``config/default.yaml`` ``memory.retrieval.embedding.enabled = false``。
  未启用或 Provider embed 不可用时不抛错，:meth:`search` 返回空列表，
  上层 :class:`HybridRetriever` 自动退化为纯 BM25。
* **零强制依赖**：余弦相似度用纯 Python 实现（不强制 numpy）；
  若环境有 numpy 则自动加速。
* **RRF 融合**：与 BM25 用 Reciprocal Rank Fusion 合并，
  ``score = Σ 1 / (60 + rank_i)``（不引入向量数据库）。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from loopforge.core.enums import MemoryLayer
from loopforge.memory.retrieval.base import BaseRetriever, IndexDoc, IndexStats, RetrievedDoc

__all__ = ["BaseEmbedder", "EmbeddingRetriever", "cosine_similarity", "reciprocal_rank_fusion"]


class BaseEmbedder(Protocol):
    """嵌入器协议 —— WP2 Provider 的 embed 接口适配。"""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """把一批文本编码成向量。

        :param texts: 文本列表。
        :returns: 与输入等长的向量列表。
        """
        ...


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """余弦相似度（纯 Python，零依赖）。

    :param left: 向量 A。
    :param right: 向量 B。
    :returns: ``[-1, 1]`` 的余弦值；零向量返回 0.0。
    """
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    norm_left = 0.0
    norm_right = 0.0
    for a, b in zip(left, right):
        dot += a * b
        norm_left += a * a
        norm_right += b * b
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_left) * math.sqrt(norm_right))


def reciprocal_rank_fusion(
    bm25_hits: Sequence[RetrievedDoc],
    vector_hits: Sequence[RetrievedDoc],
    *,
    k: int = 60,
) -> list[RetrievedDoc]:
    """RRF 融合两个检索器的结果。

    :param bm25_hits: BM25 命中（按得分降序）。
    :param vector_hits: 向量命中（按得分降序）。
    :param k: RRF 常数（默认 60）。
    :returns: 融合后按 RRF 得分降序的命中（去重）。
    """
    fused: dict[str, float] = {}
    meta: dict[str, RetrievedDoc] = {}
    for rank, hit in enumerate(bm25_hits, start=1):
        fused[hit.doc_id] = fused.get(hit.doc_id, 0.0) + 1.0 / (k + rank)
        meta[hit.doc_id] = hit
    for rank, hit in enumerate(vector_hits, start=1):
        fused[hit.doc_id] = fused.get(hit.doc_id, 0.0) + 1.0 / (k + rank)
        if hit.doc_id not in meta:
            meta[hit.doc_id] = hit
    ranked = sorted(fused.items(), key=lambda pair: pair[1], reverse=True)
    return [
        RetrievedDoc(
            doc_id=doc_id,
            score=round(score, 6),
            text=meta[doc_id].text,
            layer=meta[doc_id].layer,
            meta=dict(meta[doc_id].meta),
        )
        for doc_id, score in ranked
    ]


class EmbeddingRetriever(BaseRetriever):
    """向量检索器。

    :param embedder: 嵌入器（实现 :class:`BaseEmbedder`）。
    :param enabled: 是否启用；默认 ``False``。
    :param fallback: 不可用时降级到的检索器（通常为 BM25）；可为 ``None``。
    """

    name: str = "embedding"

    def __init__(
        self,
        embedder: BaseEmbedder | None = None,
        *,
        enabled: bool = False,
        fallback: BaseRetriever | None = None,
    ) -> None:
        """初始化向量检索器。"""
        self._embedder: BaseEmbedder | None = embedder
        self.enabled: bool = enabled
        self.fallback: BaseRetriever | None = fallback
        self._docs: dict[str, IndexDoc] = {}
        self._vectors: dict[str, list[float]] = {}

    @property
    def available(self) -> bool:
        """是否可用（启用且嵌入了第一批向量）。"""
        return self.enabled and self._embedder is not None

    async def index(self, docs: Sequence[IndexDoc]) -> IndexStats:
        """索引文档（未启用时只记文档元信息，不产生向量）。"""
        if not self.available:
            for doc in docs:
                self._docs[doc.doc_id] = doc
            return await self.stats()
        texts = [doc.text for doc in docs]
        vectors = await self._embedder.embed(texts)  # type: ignore[misc]
        for doc, vector in zip(docs, vectors):
            self._docs[doc.doc_id] = doc
            self._vectors[doc.doc_id] = vector
        return await self.stats()

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        run_id: str | None = None,
        layers: Sequence[MemoryLayer] | None = None,
        min_score: float = 0.0,
    ) -> list[RetrievedDoc]:
        """向量检索；未启用或嵌入器缺失时降级到 fallback（或返回空）。"""
        if not self.available:
            if self.fallback is not None:
                return await self.fallback.search(
                    query, top_k=top_k, run_id=run_id, layers=layers, min_score=min_score
                )
            return []
        query_vector = (await self._embedder.embed([query]))[0]  # type: ignore[misc]
        scored: list[tuple[float, IndexDoc]] = []
        for doc_id, doc in self._docs.items():
            if run_id is not None and doc.run_id != run_id:
                continue
            if layers is not None and doc.layer not in layers:
                continue
            vector = self._vectors.get(doc_id)
            if vector is None:
                continue
            score = cosine_similarity(query_vector, vector)
            if score < min_score:
                continue
            scored.append((score, doc))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            RetrievedDoc(
                doc_id=doc.doc_id,
                score=round(score, 6),
                text=doc.text,
                layer=doc.layer,
                meta=dict(doc.meta),
            )
            for score, doc in scored[:top_k]
        ]

    async def delete(self, doc_ids: Sequence[str]) -> int:
        """删除文档。"""
        deleted = 0
        for doc_id in doc_ids:
            if doc_id in self._docs:
                deleted += 1
                self._docs.pop(doc_id, None)
                self._vectors.pop(doc_id, None)
        return deleted

    async def stats(self, run_id: str | None = None) -> IndexStats:
        """索引统计。"""
        docs = self._docs.values()
        if run_id is not None:
            docs = [doc for doc in docs if doc.run_id == run_id]
        return IndexStats(
            doc_count=len(docs),
            term_count=len(self._vectors),
            avg_doc_len=0.0,
            run_id=run_id,
        )


class HybridRetriever(BaseRetriever):
    """混合检索器 —— BM25 + 向量 RRF 融合。

    :param bm25: BM25 检索器（必填）。
    :param embedding: 向量检索器（可选；默认关闭）。
    :param fusion: 融合函数，缺省 :func:`reciprocal_rank_fusion`。
    """

    name: str = "hybrid"

    def __init__(
        self,
        bm25: BaseRetriever,
        embedding: EmbeddingRetriever | None = None,
        *,
        fusion: Callable[..., list[RetrievedDoc]] = reciprocal_rank_fusion,
    ) -> None:
        """初始化混合检索器。"""
        self.bm25: BaseRetriever = bm25
        self.embedding: EmbeddingRetriever | None = embedding
        self._fusion = fusion

    async def index(self, docs: Sequence[IndexDoc]) -> IndexStats:
        """同时索引到两个检索器。"""
        bm25_stats = await self.bm25.index(docs)
        if self.embedding is not None and self.embedding.enabled:
            await self.embedding.index(docs)
        return bm25_stats

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        run_id: str | None = None,
        layers: Sequence[MemoryLayer] | None = None,
        min_score: float = 0.0,
    ) -> list[RetrievedDoc]:
        """BM25 + 向量融合检索；向量不可用时退化为纯 BM25。"""
        bm25_hits = await self.bm25.search(
            query, top_k=top_k, run_id=run_id, layers=layers, min_score=min_score
        )
        if self.embedding is None or not self.embedding.enabled:
            return bm25_hits
        vector_hits = await self.embedding.search(
            query, top_k=top_k, run_id=run_id, layers=layers, min_score=0.0
        )
        fused = self._fusion(bm25_hits, vector_hits)
        return [hit for hit in fused if hit.score >= min_score][:top_k]

    async def delete(self, doc_ids: Sequence[str]) -> int:
        """同时删除。"""
        count = await self.bm25.delete(doc_ids)
        if self.embedding is not None:
            count = max(count, await self.embedding.delete(doc_ids))
        return count

    async def stats(self, run_id: str | None = None) -> IndexStats:
        """统计（以 BM25 为准）。"""
        return await self.bm25.stats(run_id=run_id)
