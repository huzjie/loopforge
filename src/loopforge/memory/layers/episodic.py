"""L2 情景记忆 —— Step 级历史事件的结构化条目。

情景记忆是「本 Run 发生过什么」的权威记录：每条对应一次观察 /
一个 Step 结果，可被 BM25 检索，也可被三级压缩折叠进里程碑摘要。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.enums import MemoryLayer, Severity
from loopforge.core.event_types import EventType
from loopforge.models.memory import MemoryItem
from loopforge.memory.layers.base import BaseMemoryLayer

__all__ = ["EpisodicMemoryLayer"]


class EpisodicMemoryLayer(BaseMemoryLayer):
    """L2 情景记忆（Step 级历史条目）。"""

    layer = MemoryLayer.EPISODIC

    async def append(
        self,
        *,
        run_id: str,
        content: str,
        task_id: str | None = None,
        meta: dict[str, Any] | None = None,
        salience: float = 0.5,
    ) -> MemoryItem:
        """写入一条情景记忆。

        :param run_id: 所属 Run。
        :param content: 事件描述。
        :param task_id: 所属 Task。
        :param meta: 元数据（如 ``{"step_id": ..., "loop_index": ...}``）。
        :param salience: 基础显著性。
        :returns: 新增条目。
        """
        item = await self._uow.memory.add_item(
            run_id=run_id,
            content=content,
            layer=MemoryLayer.EPISODIC,
            task_id=task_id,
            tokens=self.token_size(content),
            salience=salience,
            meta=meta,
        )
        self._uow.collect_event(
            EventType.MEMORY_APPENDED,
            {
                "run_id": run_id,
                "item_id": item.id,
                "layer": MemoryLayer.EPISODIC,
                "tokens": item.tokens,
                "salience": item.salience,
                "task_id": task_id,
            },
            run_id=run_id,
            task_id=task_id,
            severity=Severity.INFO,
        )
        return item

    async def recall(
        self,
        *,
        run_id: str,
        query: str | None = None,
        top_k: int = 10,
    ) -> list[MemoryItem]:
        """召回：有查询串时优先走 BM25（由调用方注入检索器），
        否则按显著性倒序取最近 ``top_k`` 条。

        本方法默认按显著性 + 时间排序；BM25 召回由
        :class:`~loopforge.memory.retrieval.bm25.Bm25Retriever` 提供。
        """
        items = await self._uow.memory.list_by_layer(
            run_id, MemoryLayer.EPISODIC, include_compressed=False
        )
        items.sort(key=lambda item: (item.salience, item.created_at), reverse=True)
        return items[:top_k]

    async def compact(
        self,
        *,
        run_id: str,
        target_tokens: int | None = None,
        level: int = 1,
    ) -> list[MemoryItem]:
        """压缩情景记忆。

        :param level: 1=结构化裁剪；2/3 由上层 Compressor 统一处理，
            本层只负责把候选条目取出来交给上层。
        :returns: 候选条目（供上层压缩写回摘要）。
        """
        items = await self._uow.memory.list_by_layer(
            run_id, MemoryLayer.EPISODIC, include_compressed=False
        )
        items.sort(key=lambda item: (item.salience, item.created_at), reverse=True)
        if level == 1:
            return items
        return items
