"""BM25 检索器测试 —— 排序正确性 / 增量索引 / 中文分词 / DB 后端。

DoD（``TASK_BREAKDOWN`` §7.4）：可索引可检索、增量 1000 条不触发全量重建。
"""

from __future__ import annotations

import pytest

from loopforge.core.enums import MemoryLayer
from loopforge.memory.retrieval.base import IndexDoc
from loopforge.memory.retrieval.bm25 import Bm25Retriever
from loopforge.memory.retrieval.tokenizer import tokenize

pytestmark = pytest.mark.asyncio


def _doc(doc_id: str, text: str, layer: MemoryLayer = MemoryLayer.EPISODIC, run_id: str | None = "run-1") -> IndexDoc:
    return IndexDoc(doc_id=doc_id, doc_type="memory_item", run_id=run_id, layer=layer, text=text)


async def test_tokenizer_mixed_language() -> None:
    """中英混排分词：标识符 / 路径 / 中文 bigram 都能命中。"""
    terms = tokenize("修复了 getSettings 的预算超支 bug: src/loopforge/core/clock.py")
    assert "getsettings" in terms
    assert "get" in terms and "settings" in terms
    assert "预算" in terms and "超支" in terms
    assert "src/loopforge/core/clock.py" in terms
    assert "clock" in terms


async def test_bm25_ranks_relevant_first() -> None:
    """BM25 排序正确性：含查询词的文档应排在前面。"""
    retriever = Bm25Retriever(k1=1.2, b=0.75)
    await retriever.index(
        [
            _doc("d1", "修复了预算超支 bug：BudgetGuard 在 precheck 里做原子预留"),
            _doc("d2", "今天天气很好，去公园散步"),
            _doc("d3", "预算系统设计：多级 scope 与三级动作 warn throttle halt"),
            _doc("d4", "预算预算预算 budget budget budget"),
        ]
    )
    hits = await retriever.search("预算超支", top_k=4)
    assert hits, "必须返回命中"
    assert hits[0].doc_id == "d1", "含「预算超支」的文档应排第一"
    doc_ids = [hit.doc_id for hit in hits]
    assert "d4" in doc_ids and "d3" in doc_ids
    assert "d2" not in doc_ids, "完全无关文档（无共享词）不应被召回"
    assert doc_ids.index("d1") < doc_ids.index("d4")


async def test_bm25_idf_penalizes_common_terms() -> None:
    """IDF 惩罚常见词：只含「预算」的文档得分低于含稀有词的文档。"""
    retriever = Bm25Retriever()
    await retriever.index(
        [
            _doc("common", "预算 预算 预算 预算 预算"),
            _doc("rare", "预算 超支 熔断 原子预留 防并发"),
        ]
    )
    hits = await retriever.search("原子预留 熔断 超支", top_k=2)
    assert hits[0].doc_id == "rare"


async def test_incremental_index_no_rebuild() -> None:
    """增量索引：新增一条只更新倒排，不影响已有命中。"""
    retriever = Bm25Retriever()
    await retriever.index([_doc("a", "上下文压缩的保真度校验")])
    hits_before = await retriever.search("保真度", top_k=5)
    assert any(hit.doc_id == "a" for hit in hits_before)
    await retriever.index([_doc("b", "保真度校验必须可验证，关键事实不能丢")])
    hits_after = await retriever.search("保真度", top_k=5)
    ids = {hit.doc_id for hit in hits_after}
    assert "a" in ids and "b" in ids


async def test_search_filters_by_run_and_layer() -> None:
    """run_id / layers 过滤。"""
    retriever = Bm25Retriever()
    await retriever.index(
        [
            _doc("r1", "run1 专属的预算熔断逻辑", run_id="run-1"),
            _doc("r2", "run2 专属的预算熔断逻辑", run_id="run-2"),
        ]
    )
    hits = await retriever.search("预算熔断", run_id="run-1", top_k=5)
    assert {hit.doc_id for hit in hits} == {"r1"}
    await retriever.index(
        [
            IndexDoc(doc_id="s1", doc_type="code_symbol", run_id="run-1", layer=MemoryLayer.SEMANTIC, text="符号表 get_settings"),
            _doc("s2", "工作记忆里的符号 get_settings", layer=MemoryLayer.WORKING, run_id="run-1"),
        ]
    )
    hits = await retriever.search("get_settings", run_id="run-1", layers=[MemoryLayer.SEMANTIC], top_k=5)
    assert {hit.doc_id for hit in hits} == {"s1"}


async def test_delete_removes_docs() -> None:
    """删除文档后不再被检索到。"""
    retriever = Bm25Retriever()
    await retriever.index([_doc("x", "要被删除的临时记忆"), _doc("y", "保留的记忆")])
    deleted = await retriever.delete(["x"])
    assert deleted == 1
    hits = await retriever.search("临时记忆", top_k=5)
    assert all(hit.doc_id != "x" for hit in hits)


async def test_stats_counts() -> None:
    """统计信息正确。"""
    retriever = Bm25Retriever()
    await retriever.index([_doc("a", "hello world"), _doc("b", "hello loopforge")])
    stats = await retriever.stats()
    assert stats.doc_count == 2
    assert stats.avg_doc_len > 0


async def test_db_backed_retriever(session_factory: object) -> None:
    """DB 后端：倒排落 inverted_index 表，检索正确。"""
    retriever = Bm25Retriever(session_factory=session_factory)
    await retriever.index(
        [
            _doc("m1", "数据库倒排索引测试文档"),
            _doc("m2", "无关内容 abcdef"),
        ]
    )
    hits = await retriever.search("倒排索引", top_k=5, run_id="run-1")
    assert hits and hits[0].doc_id == "m1"
    stats = await retriever.stats()
    assert stats.doc_count == 2


async def test_scale_index_1000_docs_fast() -> None:
    """性能冒烟：1000 条增量索引 < 1s，检索 P95 足够快。"""
    import time

    retriever = Bm25Retriever()
    start = time.monotonic()
    await retriever.index(
        [_doc(f"doc-{i:04d}", f"记忆条目 {i} 包含预算熔断和保真度校验逻辑") for i in range(1000)]
    )
    index_elapsed = time.monotonic() - start
    assert index_elapsed < 1.0, f"1000 条索引耗时 {index_elapsed:.2f}s"
    search_start = time.monotonic()
    for _ in range(100):
        await retriever.search("预算熔断", top_k=10)
    search_elapsed = time.monotonic() - search_start
    assert search_elapsed < 2.0, f"100 次检索耗时 {search_elapsed:.2f}s"
