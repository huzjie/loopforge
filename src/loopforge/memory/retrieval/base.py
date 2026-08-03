"""检索抽象 —— ``BaseRetriever``（冻结契约 §11.7 / ARCHITECTURE §7.4）。

字段冻结，**一个字符都不能改**：

.. code-block:: python

    class BaseRetriever(abc.ABC):
        name: ClassVar[str]
        async def index(self, docs: Sequence[IndexDoc]) -> IndexStats: ...
        async def search(self, query, *, top_k=10, run_id=None,
                         layers=None, min_score=0.0) -> list[RetrievedDoc]: ...
        async def delete(self, doc_ids: Sequence[str]) -> int: ...
        async def stats(self, run_id=None) -> IndexStats: ...
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import MemoryLayer

__all__ = ["BaseRetriever", "IndexDoc", "RetrievedDoc", "IndexStats"]


class IndexDoc(BaseModel):
    """待索引文档（ARCHITECTURE §7.4 冻结）。"""

    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(description="文档 ID")
    doc_type: Literal["memory_item", "code_symbol", "milestone", "experience"] = Field(
        description="文档类型"
    )
    run_id: str | None = Field(default=None, description="所属 Run")
    layer: MemoryLayer = Field(description="记忆层")
    text: str = Field(description="文档正文")
    meta: dict[str, Any] = Field(default_factory=dict, description="附加元数据")


class RetrievedDoc(BaseModel):
    """检索命中（ARCHITECTURE §7.4 冻结）。"""

    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(description="文档 ID")
    score: float = Field(description="得分")
    text: str = Field(description="命中正文")
    layer: MemoryLayer = Field(description="记忆层")
    meta: dict[str, Any] = Field(default_factory=dict, description="附加元数据")


class IndexStats(BaseModel):
    """索引统计（冻结契约用名；DB 表 ``index_stats`` 是另一层存储）。"""

    model_config = ConfigDict(extra="forbid")

    doc_count: int = Field(default=0, ge=0, description="文档数")
    term_count: int = Field(default=0, ge=0, description="词项数")
    avg_doc_len: float = Field(default=0.0, ge=0.0, description="平均文档长度")
    run_id: str | None = Field(default=None, description="统计范围 Run")


class BaseRetriever(abc.ABC):
    """检索器抽象基类。

    :attr name: 检索器注册名（``bm25`` / ``embedding``）。
    """

    name: ClassVar[str] = "base"

    @abc.abstractmethod
    async def index(self, docs: Sequence[IndexDoc]) -> IndexStats:
        """索引一批文档（增量：重复 doc_id 覆盖）。

        :param docs: 待索引文档。
        :returns: 索引后统计。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        run_id: str | None = None,
        layers: Sequence[MemoryLayer] | None = None,
        min_score: float = 0.0,
    ) -> list[RetrievedDoc]:
        """检索。

        :param query: 查询串。
        :param top_k: 返回条数上限。
        :param run_id: 限定 Run；``None`` 表示全库。
        :param layers: 限定记忆层；``None`` 表示全部层。
        :param min_score: 最低得分过滤。
        :returns: 按得分降序的命中列表。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def delete(self, doc_ids: Sequence[str]) -> int:
        """删除文档（返回实际删除条数）。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def stats(self, run_id: str | None = None) -> IndexStats:
        """索引统计。"""
        raise NotImplementedError
