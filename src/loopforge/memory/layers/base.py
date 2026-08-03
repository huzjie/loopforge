"""记忆层抽象与共享工具。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.1（``layers/base.py``）。

:class:`BaseMemoryLayer` 定义四层记忆的统一接口：``append`` / ``recall`` /
``compact`` / ``token_size``。实现统一依赖 :class:`UnitOfWork` 与
:class:`~loopforge.db.repositories.memory_repo.MemoryRepository`。

``estimate_tokens`` 是全局 token 估算器（CJK 按字符、ASCII 按 4 字符/token），
被 pinned / compressor / manager 共用。
"""

from __future__ import annotations

import abc
import re
from collections.abc import Sequence
from typing import Any

from loopforge.core.enums import MemoryLayer
from loopforge.db.uow import UnitOfWork
from loopforge.models.memory import MemoryItem

__all__ = [
    "estimate_tokens",
    "BaseMemoryLayer",
]

#: 连续 CJK 正则。
_CJK_RE: re.Pattern[str] = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：CJK 每个字符 1 token，其余每 4 字符 1 token。

    :param text: 待估算文本。
    :returns: token 数（>=0）。
    """
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    ascii_count = len(_CJK_RE.sub("", text))
    return cjk_count + max(1, ascii_count // 4)


class BaseMemoryLayer(abc.ABC):
    """记忆层抽象基类。

    :param uow: 已进入上下文的 :class:`UnitOfWork`（调用方负责 commit）。
    """

    layer: MemoryLayer

    def __init__(self, uow: UnitOfWork) -> None:
        """初始化层。

        :param uow: 事务上下文。
        """
        self._uow: UnitOfWork = uow

    # -- 写 -----------------------------------------------------------------

    @abc.abstractmethod
    async def append(
        self,
        *,
        run_id: str,
        content: str,
        task_id: str | None = None,
        meta: dict[str, Any] | None = None,
        salience: float = 0.5,
    ) -> MemoryItem:
        """写入一条记忆。

        :param run_id: 所属 Run。
        :param content: 记忆正文。
        :param task_id: 所属 Task（可空）。
        :param meta: 附加元数据。
        :param salience: 基础显著性 0~1。
        :returns: 已 flush 的 :class:`MemoryItem`。
        """
        raise NotImplementedError

    # -- 读 -----------------------------------------------------------------

    @abc.abstractmethod
    async def recall(
        self,
        *,
        run_id: str,
        query: str | None = None,
        top_k: int = 10,
    ) -> list[MemoryItem]:
        """召回本层记忆。

        :param run_id: 所属 Run。
        :param query: 检索查询串（层内实现可忽略）。
        :param top_k: 最大条数。
        :returns: 记忆条目列表。
        """
        raise NotImplementedError

    # -- 压缩 ---------------------------------------------------------------

    @abc.abstractmethod
    async def compact(
        self,
        *,
        run_id: str,
        target_tokens: int | None = None,
        level: int = 1,
    ) -> Any:
        """压缩本层记忆。

        :param run_id: 所属 Run。
        :param target_tokens: 目标 token 数。
        :param level: 压缩级别 1/2/3。
        :returns: 压缩结果（层相关）。
        """
        raise NotImplementedError

    # -- token --------------------------------------------------------------

    def token_size(self, text: str) -> int:
        """估算一段文本的 token 数。"""
        return estimate_tokens(text)

    # -- 工具 ---------------------------------------------------------------

    async def count_tokens(self, run_id: str) -> int:
        """统计本层未压缩记忆的总 token 数。"""
        return await self._uow.memory.total_tokens(run_id, layer=self.layer)


class _DummyLayer(BaseMemoryLayer):
    """占位实现 —— 仅供 ``@abstractmethod`` 之外的兜底使用，不对外暴露。"""

    layer = MemoryLayer.WORKING

    async def append(self, **kwargs: Any) -> MemoryItem:  # pragma: no cover
        raise NotImplementedError

    async def recall(self, **kwargs: Any) -> list[MemoryItem]:  # pragma: no cover
        return []

    async def compact(self, **kwargs: Any) -> None:  # pragma: no cover
        return None


def layer_for(uow: UnitOfWork, layer: MemoryLayer) -> BaseMemoryLayer:
    """按枚举取层实现（延迟 import 避免循环依赖）。

    :param uow: 事务上下文。
    :param layer: 记忆层。
    :returns: 对应的层实例。
    """
    from loopforge.memory.layers.working import WorkingMemoryLayer
    from loopforge.memory.layers.episodic import EpisodicMemoryLayer
    from loopforge.memory.layers.semantic import SemanticMemoryLayer
    from loopforge.memory.layers.milestone import MilestoneLayer

    mapping: dict[MemoryLayer, type[BaseMemoryLayer]] = {
        MemoryLayer.WORKING: WorkingMemoryLayer,
        MemoryLayer.EPISODIC: EpisodicMemoryLayer,
        MemoryLayer.SEMANTIC: SemanticMemoryLayer,
        MemoryLayer.MILESTONE: MilestoneLayer,
    }
    factory = mapping.get(layer)
    if factory is None:
        raise ValueError(f"没有对应的层实现：{layer}")
    return factory(uow)


__all__ = ["estimate_tokens", "BaseMemoryLayer", "layer_for"]
