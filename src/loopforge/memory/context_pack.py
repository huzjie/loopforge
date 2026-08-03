"""上下文装配产物 —— ``ContextPack``。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.1、``docs/ARCHITECTURE.md`` §8.2.5。

``ContextPack`` 是 :meth:`ContextManager.build_context` 的返回类型，
结构按 ARCHITECTURE 时序图冻结为 ``{messages, layer_tokens, dropped_items[]}``。

* ``messages`` —— 可直接交给 Provider 的分层消息列表（role/content 字典）。
* ``layer_tokens`` —— 每个记忆层实际消耗的 token 数，便于配额审计。
* ``dropped_items`` —— 因预算被裁剪掉的候选清单（绝不静默丢弃）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import MemoryLayer

__all__ = ["ContextBlock", "DroppedItem", "ContextPack"]


class ContextBlock(BaseModel):
    """装配后的一个上下文块。

    :param source: 来源标识：``pinned`` / ``working`` / ``episodic`` /
        ``semantic`` / ``milestone`` / ``task``。
    :param layer: 对应的记忆层；任务说明等非记忆内容为 ``None``。
    :param content: 块正文（已按层截断策略处理）。
    :param tokens: 块 token 估算。
    :param ref_id: 来源实体 ID（记忆条目 ID / 摘要 ID / 约束 ID）。
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="来源标识")
    layer: MemoryLayer | None = Field(default=None, description="对应记忆层")
    content: str = Field(description="块正文")
    tokens: int = Field(default=0, ge=0, description="块 token 数")
    ref_id: str | None = Field(default=None, description="来源实体 ID")

    def as_message(self, role: str = "user") -> dict[str, str]:
        """转成单条 OpenAI 风格消息。"""
        return {"role": role, "content": self.content}


class DroppedItem(BaseModel):
    """因预算被裁剪掉的候选（审计与调优用）。"""

    model_config = ConfigDict(extra="forbid")

    ref_id: str = Field(description="被裁剪实体 ID")
    layer: MemoryLayer | None = Field(default=None, description="所属记忆层")
    reason: str = Field(default="budget", description="裁剪原因")
    tokens: int = Field(default=0, ge=0, description="被裁剪掉的 token 数")


class ContextPack(BaseModel):
    """分层上下文包 —— ``build_context`` 的返回值。

    ``to_messages()`` 把全部块按顺序渲染成消息列表；``render()`` 拼成纯文本。
    """

    model_config = ConfigDict(extra="forbid")

    messages: list[dict[str, str]] = Field(
        default_factory=list, description="可直接投喂 Provider 的消息"
    )
    layer_tokens: dict[MemoryLayer, int] = Field(
        default_factory=dict, description="各层实际 token 占用"
    )
    dropped_items: list[DroppedItem] = Field(
        default_factory=list, description="被裁剪项清单"
    )
    total_tokens: int = Field(default=0, ge=0, description="总 token 数")
    budget_tokens: int = Field(default=0, ge=0, description="本次装配的 token 预算")

    @property
    def dropped_count(self) -> int:
        """被裁剪掉的候选条数。"""
        return len(self.dropped_items)

    def to_messages(self) -> list[dict[str, str]]:
        """返回消息列表（与 ``messages`` 一致，便于语义化调用）。"""
        return self.messages

    def render(self, separator: str = "\n\n") -> str:
        """把所有消息拼成一段纯文本（调试 / 记录用）。"""
        return separator.join(
            message.get("content", "") for message in self.messages if message.get("content")
        )

    def tokens_for(self, layer: MemoryLayer) -> int:
        """某层实际 token 占用。"""
        return self.layer_tokens.get(layer, 0)

    @classmethod
    def from_blocks(
        cls,
        blocks: Iterable[ContextBlock],
        *,
        budget_tokens: int,
        dropped: Iterable[DroppedItem] | None = None,
        system_prompt: str | None = None,
    ) -> ContextPack:
        """从有序块构造 ContextPack。

        :param blocks: 已按优先级排序的上下文块。
        :param budget_tokens: 本次装配预算。
        :param dropped: 被裁剪掉的候选。
        :param system_prompt: 可选的 system 消息（置于最前）。
        """
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        layer_tokens: dict[MemoryLayer, int] = {}
        total = 0
        for block in blocks:
            messages.append(block.as_message())
            total += block.tokens
            if block.layer is not None:
                layer_tokens[block.layer] = layer_tokens.get(block.layer, 0) + block.tokens
        return cls(
            messages=messages,
            layer_tokens=layer_tokens,
            dropped_items=list(dropped or []),
            total_tokens=total,
            budget_tokens=budget_tokens,
        )

    def summary(self) -> Mapping[str, Any]:
        """机器可读的装配摘要（审计日志 / 事件 payload 用）。"""
        return {
            "total_tokens": self.total_tokens,
            "budget_tokens": self.budget_tokens,
            "layer_tokens": {layer.value: tokens for layer, tokens in self.layer_tokens.items()},
            "dropped_count": self.dropped_count,
            "message_count": len(self.messages),
        }
