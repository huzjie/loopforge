"""L1 工作记忆 —— 当前循环即时上下文。

容量受限：默认保留最近 ``capacity`` 条未压缩条目（FIFO 滚动窗口）；
超过容量时按「低显著性优先淘汰」补一刀（FIFO + 重要性淘汰）。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.enums import MemoryLayer, Severity
from loopforge.core.event_types import EventType
from loopforge.models.memory import MemoryItem
from loopforge.memory.layers.base import BaseMemoryLayer

__all__ = ["WorkingMemoryLayer", "DEFAULT_WORKING_CAPACITY"]

#: 工作记忆默认容量（条目数）。
DEFAULT_WORKING_CAPACITY: int = 40


class WorkingMemoryLayer(BaseMemoryLayer):
    """L1 工作记忆（当前 Loop 滚动窗口）。"""

    layer = MemoryLayer.WORKING

    def __init__(self, uow: Any, *, capacity: int = DEFAULT_WORKING_CAPACITY) -> None:
        """初始化。

        :param uow: 事务上下文。
        :param capacity: 最大保留条目数。
        """
        super().__init__(uow)
        self.capacity: int = max(1, capacity)

    async def append(
        self,
        *,
        run_id: str,
        content: str,
        task_id: str | None = None,
        meta: dict[str, Any] | None = None,
        salience: float = 0.5,
    ) -> MemoryItem:
        """写入一条工作记忆，并在超容量时执行 FIFO + 重要性淘汰。

        :param run_id: 所属 Run。
        :param content: 记忆正文。
        :param task_id: 所属 Task。
        :param meta: 元数据。
        :param salience: 基础显著性。
        :returns: 新增条目。
        """
        item = await self._uow.memory.add_item(
            run_id=run_id,
            content=content,
            layer=MemoryLayer.WORKING,
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
                "layer": MemoryLayer.WORKING,
                "tokens": item.tokens,
                "salience": item.salience,
                "task_id": task_id,
            },
            run_id=run_id,
            task_id=task_id,
            severity=Severity.INFO,
        )
        await self._enforce_capacity(run_id)
        return item

    async def _enforce_capacity(self, run_id: str) -> None:
        """容量控制：超出容量时按「旧 → 低显著性」淘汰，但**不物理删除**。

        工作记忆的淘汰是把条目标记为已压缩（``compressed_into`` 指向
        一个虚拟 id ``evicted:working``），原文仍留在库中。
        """
        items = await self._uow.memory.list_by_layer(
            run_id, MemoryLayer.WORKING, include_compressed=False
        )
        if len(items) <= self.capacity:
            return
        # FIFO 优先淘汰最旧；同批次内显著性低的先淘汰。
        ordered = sorted(
            items, key=lambda item: (item.created_at, item.salience)
        )
        excess = ordered[: len(items) - self.capacity]
        await self._uow.memory.mark_compressed(
            [item.id for item in excess], summary_id="evicted:working"
        )

    async def recall(
        self,
        *,
        run_id: str,
        query: str | None = None,
        top_k: int = 10,
    ) -> list[MemoryItem]:
        """召回最近的 ``top_k`` 条工作记忆（时间倒序）。"""
        items = await self._uow.memory.list_by_layer(
            run_id, MemoryLayer.WORKING, include_compressed=False
        )
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items[:top_k]

    async def compact(
        self,
        *,
        run_id: str,
        target_tokens: int | None = None,
        level: int = 1,
    ) -> None:
        """L1 只做结构化裁剪（level 固定为 1，符合 M4 分级降级）。"""
        if level != 1:
            return
        items = await self.recall(run_id=run_id, top_k=self.capacity)
        from loopforge.memory.compressor import Compressor

        compressor = Compressor()
        trimmed = compressor.compress_level1(items)
        # 把裁剪后内容写回工作记忆（原文保留在库中，压缩链通过 meta 记录）。
        if trimmed:
            digest = f"l1:{items[0].id if items else 'none'}"
            await self._uow.memory.add_item(
                run_id=run_id,
                content="\n".join(chunk.content for chunk in trimmed),
                layer=MemoryLayer.WORKING,
                tokens=sum(chunk_meta_tokens(chunk) for chunk in trimmed),
                salience=0.8,
                meta={"kind": "l1_compacted", "source_digest": digest},
            )


def chunk_meta_tokens(chunk: Any) -> int:
    """从 MemoryChunk 估算 token 数（供 L1 写回用）。"""
    from loopforge.memory.layers.base import estimate_tokens

    return estimate_tokens(chunk.content)
