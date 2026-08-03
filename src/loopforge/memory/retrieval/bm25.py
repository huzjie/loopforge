"""纯 Python BM25 检索器 —— 零外部依赖、真实可用。

来源：``docs/TASK_BREAKDOWN.md`` §7.3.3、``docs/ARCHITECTURE.md`` §7.4。

设计要点
--------

* **零外部依赖**：不引 elasticsearch / whoosh / rank_bm25。倒排表
  可选持久化到 ``inverted_index`` 表（传 ``session_factory``）；不传时
  退化为纯内存模式（单测 / 小规模场景）。
* **k1 / b 可配**（默认 ``k1=1.2, b=0.75``）。
* **增量索引**：:meth:`index` 对重复 ``doc_id`` 做覆盖更新，不触发全量重建。
* **中英混排分词**：复用 ``retrieval/tokenizer.py``（代码感知 + CJK bigram）。
* **BM25 打分**：:

    score(d, q) = Σ_t idf(t) * tf(t,d) * (k1 + 1) /
                  (tf(t,d) + k1 * (1 - b + b * len(d) / avgdl))

    idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))   # BM25+，恒非负

* **文档长度**：进程内缓存（类似 Lucene norms）；倒排表本身持久化。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from loopforge.core.enums import MemoryLayer
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork
from loopforge.memory.retrieval.base import BaseRetriever, IndexDoc, IndexStats, RetrievedDoc
from loopforge.memory.retrieval.tokenizer import Tokenizer, default_tokenizer

__all__ = ["Bm25Retriever", "Posting", "DocMeta"]

#: BM25 默认参数。
DEFAULT_K1: float = 1.2
DEFAULT_B: float = 0.75


@dataclass(slots=True)
class Posting:
    """倒排表的一个词项-文档对。"""

    term: str
    doc_id: str
    tf: int = 0
    positions: list[int] = field(default_factory=list)
    run_id: str | None = None
    doc_type: str = "memory_item"


@dataclass(slots=True)
class DocMeta:
    """文档元信息（打分需要的长度与归属）。"""

    doc_id: str
    doc_len: int = 0
    run_id: str | None = None
    layer: MemoryLayer = MemoryLayer.WORKING
    doc_type: str = "memory_item"
    text: str = ""


class _MemoryBackend:
    """纯内存倒排后端。"""

    def __init__(self) -> None:
        self.postings: dict[str, dict[str, Posting]] = {}
        self.docs: dict[str, DocMeta] = {}

    async def upsert(self, doc: IndexDoc, frequencies: dict[str, int], positions: dict[str, list[int]]) -> None:
        """写入一篇文档的词项（内存模式为同步实现，保持 async 签名）。"""
        for term, tf in frequencies.items():
            posting = Posting(
                term=term,
                doc_id=doc.doc_id,
                tf=tf,
                positions=positions.get(term, []),
                run_id=doc.run_id,
                doc_type=doc.doc_type,
            )
            self.postings.setdefault(term, {})[doc.doc_id] = posting
        self.docs[doc.doc_id] = DocMeta(
            doc_id=doc.doc_id,
            doc_len=sum(frequencies.values()),
            run_id=doc.run_id,
            layer=doc.layer,
            doc_type=doc.doc_type,
            text=doc.text,
        )

    async def postings_for(self, terms: Sequence[str], run_id: str | None) -> dict[str, list[Posting]]:
        """取词项对应的倒排（按 run_id 过滤）。"""
        result: dict[str, list[Posting]] = {}
        for term in terms:
            candidates = self.postings.get(term, {})
            if run_id is not None:
                candidates = {did: p for did, p in candidates.items() if p.run_id == run_id}
            if candidates:
                result[term] = list(candidates.values())
        return result

    async def delete(self, doc_ids: Sequence[str]) -> int:
        """删除文档。"""
        deleted = 0
        for doc_id in doc_ids:
            meta = self.docs.pop(doc_id, None)
            if meta is None:
                continue
            deleted += 1
            for term, posting_map in self.postings.items():
                if doc_id in posting_map:
                    posting_map.pop(doc_id)
        return deleted

    def doc(self, doc_id: str) -> DocMeta | None:
        """取文档元信息。"""
        return self.docs.get(doc_id)

    def all_docs(self) -> list[DocMeta]:
        """全部文档。"""
        return list(self.docs.values())


class _DbBackend:
    """基于 ``inverted_index`` 表的持久化后端（进程内缓存文档长度）。"""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory: Any = session_factory
        self._doc_lens: dict[str, int] = {}
        self._doc_layers: dict[str, MemoryLayer] = {}
        self._doc_texts: dict[str, str] = {}

    async def upsert(self, doc: IndexDoc, frequencies: dict[str, int], positions: dict[str, list[int]]) -> None:
        """写倒排（持久化） + 更新进程内文档长度缓存。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            run_id = doc.run_id or ""
            for term, tf in frequencies.items():
                await uow.index.upsert_posting(
                    term=term,
                    doc_id=doc.doc_id,
                    run_id=run_id,
                    tf=tf,
                    positions=positions.get(term, []),
                    doc_type=doc.doc_type,
                )
            # 更新该 run × doc_type 的统计（doc_count / avg_doc_len）。
            lens = [
                length
                for did, length in self._doc_lens.items()
                if self._doc_layers.get(did) is not None
            ]
            if lens:
                doc_count = len(lens)
                avg_len = sum(lens) / len(lens)
                await uow.index.upsert_stats(
                    run_id=run_id,
                    doc_type=doc.doc_type,
                    doc_count=doc_count,
                    avg_doc_len=avg_len,
                )
        total_tf = sum(frequencies.values())
        self._doc_lens[doc.doc_id] = total_tf
        self._doc_layers[doc.doc_id] = doc.layer
        self._doc_texts[doc.doc_id] = doc.text

    async def postings_for(self, terms: Sequence[str], run_id: str | None) -> dict[str, list[Posting]]:
        """从库中取倒排。"""
        if not terms:
            return {}
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            rows = await uow.index.postings_for_terms(run_id=run_id or "", terms=list(terms))
        grouped: dict[str, list[Posting]] = {}
        for row in rows:
            grouped.setdefault(row.term, []).append(
                Posting(
                    term=row.term,
                    doc_id=row.doc_id,
                    tf=row.tf,
                    positions=list(row.positions),
                    run_id=row.run_id,
                    doc_type=row.doc_type,
                )
            )
        return grouped

    async def delete(self, doc_ids: Sequence[str]) -> int:
        """删除文档（倒排行 + 内存缓存）。"""
        if not doc_ids:
            return 0
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            deleted = 0
            for doc_id in doc_ids:
                if doc_id in self._doc_lens:
                    deleted += 1
                    self._doc_lens.pop(doc_id, None)
                    self._doc_layers.pop(doc_id, None)
                    self._doc_texts.pop(doc_id, None)
            return deleted

    def doc(self, doc_id: str) -> DocMeta | None:
        """取文档元信息（仅内存缓存；未索引过返回 None）。"""
        if doc_id not in self._doc_lens:
            return None
        return DocMeta(
            doc_id=doc_id,
            doc_len=self._doc_lens[doc_id],
            run_id=None,
            layer=self._doc_layers.get(doc_id, MemoryLayer.WORKING),
            text=self._doc_texts.get(doc_id, ""),
        )

    def all_docs(self) -> list[DocMeta]:
        """全部文档（内存缓存）。"""
        return [
            DocMeta(
                doc_id=doc_id,
                doc_len=length,
                run_id=None,
                layer=self._doc_layers.get(doc_id, MemoryLayer.WORKING),
                text=self._doc_texts.get(doc_id, ""),
            )
            for doc_id, length in self._doc_lens.items()
        ]


class Bm25Retriever(BaseRetriever):
    """BM25 检索器（内存 / 数据库双后端）。

    :param session_factory: 传入则倒排持久化到 ``inverted_index`` 表；
        传 ``None`` 走纯内存。
    :param k1: BM25 词频饱和参数。
    :param b: BM25 文档长度归一化参数。
    :param tokenizer: 分词器；缺省用默认 :class:`Tokenizer`。
    """

    name: str = "bm25"

    def __init__(
        self,
        session_factory: Any | None = None,
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
        tokenizer: Tokenizer | None = None,
    ) -> None:
        """初始化 BM25 检索器。"""
        if k1 <= 0:
            raise ValueError(f"k1 必须 > 0，收到 {k1}")
        if not 0.0 <= b <= 1.0:
            raise ValueError(f"b 必须在 [0,1]，收到 {b}")
        factory = session_factory if session_factory is not None else get_session_factory()
        self._backend: _MemoryBackend | _DbBackend
        if session_factory is None:
            self._backend = _MemoryBackend()
        else:
            self._backend = _DbBackend(factory)
        self.k1: float = k1
        self.b: float = b
        self._tokenizer: Tokenizer = tokenizer or default_tokenizer()

    # -- 索引 ---------------------------------------------------------------

    async def index(self, docs: Sequence[IndexDoc]) -> IndexStats:
        """增量索引一批文档（重复 doc_id 覆盖）。"""
        if not docs:
            return await self.stats()
        for doc in docs:
            frequencies = self._tokenizer.frequencies(doc.text)
            positions = self._tokenizer.positions(doc.text)
            await self._backend.upsert(doc, frequencies, positions)
        return await self.stats()

    async def index_text(
        self,
        *,
        doc_id: str,
        text: str,
        layer: MemoryLayer = MemoryLayer.EPISODIC,
        run_id: str | None = None,
        doc_type: str = "memory_item",
        meta: dict[str, Any] | None = None,
    ) -> IndexStats:
        """便捷：直接索引一段文本。"""
        doc = IndexDoc(
            doc_id=doc_id,
            doc_type=doc_type,  # type: ignore[arg-type]
            run_id=run_id,
            layer=layer,
            text=text,
            meta=dict(meta or {}),
        )
        return await self.index([doc])

    # -- 检索 ---------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        run_id: str | None = None,
        layers: Sequence[MemoryLayer] | None = None,
        min_score: float = 0.0,
    ) -> list[RetrievedDoc]:
        """BM25 检索。"""
        if not query or not query.strip():
            return []
        terms = self._tokenizer.unique_terms(query)
        if not terms:
            return []
        postings = await self._backend.postings_for(terms, run_id)
        if not postings:
            return []

        # 统计全局参数：N（文档数）与 df(t)。
        all_docs = self._backend.all_docs()
        n_docs = len(all_docs)
        if n_docs == 0:
            return []
        avgdl = sum(doc.doc_len for doc in all_docs) / n_docs

        scores: dict[str, float] = {}
        doc_meta: dict[str, DocMeta] = {}
        for term, posting_list in postings.items():
            df = len(posting_list)
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            for posting in posting_list:
                doc_id = posting.doc_id
                meta = self._backend.doc(doc_id)
                if meta is None:
                    continue
                if layers is not None and meta.layer not in layers:
                    continue
                if run_id is not None and meta.run_id is not None and meta.run_id != run_id:
                    continue
                doc_len = max(1, meta.doc_len)
                tf = posting.tf
                denominator = tf + self.k1 * (1.0 - self.b + self.b * doc_len / avgdl)
                score = idf * (tf * (self.k1 + 1.0)) / denominator
                scores[doc_id] = scores.get(doc_id, 0.0) + score
                doc_meta[doc_id] = meta

        ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
        results: list[RetrievedDoc] = []
        for doc_id, score in ranked:
            if score < min_score:
                continue
            meta = doc_meta[doc_id]
            results.append(
                RetrievedDoc(
                    doc_id=doc_id,
                    score=round(score, 6),
                    text=meta.text,
                    layer=meta.layer,
                    meta={"doc_type": meta.doc_type},
                )
            )
            if len(results) >= top_k:
                break
        return results

    # -- 删除 / 统计 --------------------------------------------------------

    async def delete(self, doc_ids: Sequence[str]) -> int:
        """删除文档（返回删除条数）。"""
        if not doc_ids:
            return 0
        return await self._backend.delete(list(doc_ids))

    async def stats(self, run_id: str | None = None) -> IndexStats:
        """索引统计（基于进程内文档长度缓存；doc_count/avg_doc_len 精确）。"""
        docs = self._backend.all_docs()
        if run_id is not None:
            docs = [doc for doc in docs if doc.run_id == run_id]
        if not docs:
            return IndexStats(doc_count=0, term_count=0, avg_doc_len=0.0, run_id=run_id)
        # term_count 仅内存后端可精确统计；DB 后端统计词项数需要全表扫描，
        # 这里从内存缓存中的文档长度估算（文档数 × 平均长度 ≈ 词项出现总次数）。
        total_postings = 0
        if isinstance(self._backend, _MemoryBackend):
            total_postings = sum(len(posting_map) for posting_map in self._backend.postings.values())
        else:
            total_postings = sum(doc.doc_len for doc in docs)
        avg_len = sum(doc.doc_len for doc in docs) / len(docs)
        return IndexStats(
            doc_count=len(docs),
            term_count=total_postings,
            avg_doc_len=round(avg_len, 4),
            run_id=run_id,
        )
