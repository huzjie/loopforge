"""检索器注册 —— 约定式自动发现（``TASK_BREAKDOWN`` §13.3）。

:data:`retriever_registry` 是全局检索器注册表；新增检索器 = 新建一个
继承 :class:`~loopforge.memory.retrieval.base.BaseRetriever` 的模块，
无需修改本文件（由上层 ``autoload`` 或显式注册完成）。
"""

from __future__ import annotations

from loopforge.core.registry import Registry
from loopforge.memory.retrieval.base import (
    BaseRetriever,
    IndexDoc,
    IndexStats,
    RetrievedDoc,
)
from loopforge.memory.retrieval.tokenizer import Tokenizer, default_tokenizer, tokenize

__all__ = [
    "BaseRetriever",
    "IndexDoc",
    "IndexStats",
    "RetrievedDoc",
    "Tokenizer",
    "default_tokenizer",
    "retriever_registry",
    "tokenize",
]

#: 全局检索器注册表（名称：类或实例工厂）。
retriever_registry: Registry[type[BaseRetriever] | BaseRetriever] = Registry("retriever")


def autoload_retrievers() -> None:
    """注册内置检索器（幂等，可多次调用）。"""
    from loopforge.memory.retrieval.bm25 import Bm25Retriever
    from loopforge.memory.retrieval.embedding_retriever import EmbeddingRetriever, HybridRetriever

    for name, cls in (
        ("bm25", Bm25Retriever),
        ("embedding", EmbeddingRetriever),
        ("hybrid", HybridRetriever),
    ):
        if name not in retriever_registry:
            retriever_registry.register(name, cls)


autoload_retrievers()
