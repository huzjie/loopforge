"""L4 里程碑摘要层 —— 递归摘要树的读取与生成。

里程碑摘要是长程运行的阶段性快照：每条摘要覆盖一段事件区间
（``covers_from_seq`` ~ ``covers_to_seq``），按 ``level`` 递归。
恢复与回顾时优先取最高层摘要（信息密度最高）。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.enums import MemoryLayer
from loopforge.models.memory import MilestoneSummary
from loopforge.memory.layers.base import BaseMemoryLayer

__all__ = ["MilestoneLayer"]


class MilestoneLayer(BaseMemoryLayer):
    """L4 里程碑摘要层（基于 :class:`MilestoneSummaryRepository`）。"""

    layer = MemoryLayer.MILESTONE

    async def append(  # type: ignore[override]
        self,
        *,
        run_id: str,
        content: str,
        task_id: str | None = None,
        meta: dict[str, Any] | None = None,
        salience: float = 0.5,
    ) -> MilestoneSummary:
        """写入一条里程碑摘要（覆盖区间由调用方通过 meta 指定）。

        :param run_id: 所属 Run。
        :param content: 摘要正文。
        :param task_id: 忽略（摘要属于 Run 级）。
        :param meta: 需含 ``level`` / ``covers_from_seq`` / ``covers_to_seq`` /
            ``source_item_ids``。
        :param salience: 忽略（摘要的优先级由 level 决定）。
        :returns: 已 flush 的 :class:`MilestoneSummary`。
        """
        meta = dict(meta or {})
        level = int(meta.get("level", 1))
        covers_from = int(meta.get("covers_from_seq", 0))
        covers_to = int(meta.get("covers_to_seq", covers_from))
        source_item_ids = list(meta.get("source_item_ids", []))
        summary = MilestoneSummary(
            run_id=run_id,
            level=max(0, level),
            covers_from_seq=max(0, covers_from),
            covers_to_seq=max(0, max(covers_from, covers_to)),
            content=content,
            tokens=self.token_size(content),
            source_item_ids=source_item_ids,
        )
        self._uow.session.add(summary)
        await self._uow.session.flush()
        return summary

    async def recall(
        self,
        *,
        run_id: str,
        query: str | None = None,
        top_k: int = 10,
    ) -> list[MilestoneSummary]:
        """召回：最高层摘要优先（信息密度高），同级按时间倒序。"""
        summaries = await self._uow.milestones.list_by_level(
            run_id, level=0, limit=top_k * 4
        )
        # 兜底：level 0 不满时从更高层补。
        if len(summaries) < top_k:
            for level in (1, 2, 3):
                extra = await self._uow.milestones.list_by_level(
                    run_id, level=level, limit=top_k
                )
                summaries.extend(extra)
                if len(summaries) >= top_k:
                    break
        summaries.sort(key=lambda item: item.created_at, reverse=True)
        return summaries[:top_k]

    async def latest(self, run_id: str) -> MilestoneSummary | None:
        """取最新一条摘要（恢复回顾用）。"""
        summaries = await self.recall(run_id=run_id, top_k=1)
        return summaries[0] if summaries else None

    async def compact(
        self,
        *,
        run_id: str,
        target_tokens: int | None = None,
        level: int = 1,
    ) -> MilestoneSummary | None:
        """生成更高层摘要：把现有最高层摘要再折叠一层（level+1）。

        :param run_id: 所属 Run。
        :param target_tokens: 目标 token 数。
        :param level: 忽略（生成层数自动 +1）。
        :returns: 新生成的摘要；无既有摘要时返回 ``None``。
        """
        all_levels: list[MilestoneSummary] = []
        for candidate_level in (0, 1, 2, 3):
            all_levels.extend(
                await self._uow.milestones.list_by_level(run_id, level=candidate_level)
            )
        if not all_levels:
            return None
        all_levels.sort(key=lambda item: item.created_at)
        from loopforge.memory.compressor import Compressor
        from loopforge.memory.summarizer import Summarizer

        compressor = Compressor()
        combined = "\n".join(item.content for item in all_levels)
        text = Summarizer().summarize_text(combined, target_tokens=target_tokens)
        next_level = max(item.level for item in all_levels) + 1
        return await self.append(
            run_id=run_id,
            content=text,
            meta={
                "level": next_level,
                "covers_from_seq": min(item.covers_from_seq for item in all_levels),
                "covers_to_seq": max(item.covers_to_seq for item in all_levels),
                "source_item_ids": [item.id for item in all_levels],
            },
        )
