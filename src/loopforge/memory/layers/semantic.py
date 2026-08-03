"""L3 语义记忆 —— 代码库知识与跨 Run 经验。

内容：符号表 / 接口签名 / README 要点 / 常见错误修复模式。
压缩策略：**重建而非丢弃** —— 语义记忆超预算时重新提炼（降噪），
不把原文折叠进摘要（语义记忆本身就是提炼产物）。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.enums import MemoryLayer, Severity
from loopforge.core.event_types import EventType
from loopforge.models.memory import MemoryItem
from loopforge.memory.layers.base import BaseMemoryLayer

__all__ = ["SemanticMemoryLayer"]


class SemanticMemoryLayer(BaseMemoryLayer):
    """L3 语义记忆。"""

    layer = MemoryLayer.SEMANTIC

    async def append(
        self,
        *,
        run_id: str,
        content: str,
        task_id: str | None = None,
        meta: dict[str, Any] | None = None,
        salience: float = 0.5,
    ) -> MemoryItem:
        """写入一条语义记忆。

        :param run_id: 所属 Run（跨 Run 经验可挂在 ``run_id="__global__"``）。
        :param content: 知识 / 约定 / API 用法。
        :param task_id: 所属 Task。
        :param meta: 元数据（如 ``{"kind": "api_usage"}``）。
        :param salience: 基础显著性。
        :returns: 新增条目。
        """
        item = await self._uow.memory.add_item(
            run_id=run_id,
            content=content,
            layer=MemoryLayer.SEMANTIC,
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
                "layer": MemoryLayer.SEMANTIC,
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
        """召回：按显著性倒序；跨 Run 时显式传 ``run_id="__global__"``。

        .. warning::
            默认**不跨 Run**（检索默认不含 SEMANTIC，防止无意识的信息泄漏，
            见 ``schemas/memory.py`` ``DEFAULT_SEARCH_LAYERS`` 注释）。
        """
        items = await self._uow.memory.list_by_layer(
            run_id, MemoryLayer.SEMANTIC, include_compressed=False
        )
        items.sort(key=lambda item: (item.salience, item.created_at), reverse=True)
        return items[:top_k]

    async def compact(
        self,
        *,
        run_id: str,
        target_tokens: int | None = None,
        level: int = 1,
    ) -> None:
        """语义记忆压缩 = 重建（只保留高显著性条目，不产生摘要折叠）。

        :param level: 忽略；语义层没有「折叠进摘要」的概念。
        """
        items = await self._uow.memory.list_by_layer(
            run_id, MemoryLayer.SEMANTIC, include_compressed=False
        )
        # 保留显著性 >= 0.6 的条目，其余标记为已压缩（重建降噪）。
        keep = [item for item in items if item.salience >= 0.6]
        drop = [item for item in items if item.salience < 0.6]
        if drop:
            await self._uow.memory.mark_compressed(
                [item.id for item in drop], summary_id="rebuilt:semantic"
            )
        return None
