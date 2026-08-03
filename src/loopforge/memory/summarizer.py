"""摘要器 —— 摘要树（level 0..N）与 L3 生成式压缩。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.1、``docs/ARCHITECTURE.md`` §8.2.3。

设计：:class:`Summarizer` 同时支持两种后端：

* **确定性抽取**（默认，零 LLM 成本）—— 按关键词密度 + 位置给句子打分，
  贪心装填直到目标 token 数。与 L2 的区别是：L2 只抽「决策/错误/路径」
  特征句，L3 会做**递归折叠**（摘要的摘要）。
* **LLM 回调**（可选）—— 调用方注入 ``llm_summarize(text) -> str``
  （WP4 的 Summarizer 角色会提供），本类只负责调用与兜底。

``build_summary_tree`` 把一批记忆折叠成一棵摘要树：level 0 是原始条目，
level 1 是第一批摘要，level N 是摘要的摘要。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from loopforge.memory.layers.base import estimate_tokens
from loopforge.memory.compressor import MemoryChunk, chunks_from, extract_key_facts

__all__ = [
    "Summarizer",
    "SummaryNode",
    "summarize_by_extraction",
    "score_sentences",
]

#: L3 摘要句打分关键词。
_L3_KEYWORDS: re.Pattern[str] = re.compile(
    r"(错误|失败|修复|决定|关键|约束|必须|禁止|error|fail|fix|must|never|"
    r"constraint|bug|crash|test|pass|fail|api|接口|签名|参数|配置|部署)"
)


def score_sentences(text: str) -> list[tuple[float, str]]:
    """按关键词密度 + 位置给句子打分。

    :param text: 待摘要文本。
    :returns: ``[(分数, 句子), ...]``，已按分数降序。
    """
    sentences = re.split(r"(?<=[。！？!?.])\s*|\n+", text)
    scored: list[tuple[float, str]] = []
    total = len(sentences)
    for index, raw in enumerate(sentences):
        sentence = raw.strip()
        if len(sentence) < 6:
            continue
        keyword_hits = len(_L3_KEYWORDS.findall(sentence.lower()))
        density = keyword_hits / max(1.0, len(sentence))
        # 位置加权：开头与结尾更可能承载要点。
        position_bonus = 1.5 if index < max(1, total // 4) else (1.2 if index >= total - 1 else 0.6)
        score = density * 20.0 + position_bonus + min(2.0, len(sentence) / 200.0)
        scored.append((score, sentence))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def summarize_by_extraction(
    text: str,
    *,
    target_tokens: int | None = None,
    max_sentences: int = 12,
) -> str:
    """确定性抽取摘要（零 LLM 成本）。

    :param text: 待摘要文本。
    :param target_tokens: 目标 token 数。
    :param max_sentences: 句子数上限。
    :returns: 摘要文本。
    """
    if not text:
        return ""
    budget = target_tokens if target_tokens and target_tokens > 0 else 10**9
    picked: list[str] = []
    used = 0
    for _, sentence in score_sentences(text):
        if len(picked) >= max_sentences:
            break
        tokens = estimate_tokens(sentence)
        if used + tokens > budget:
            break
        picked.append(sentence)
        used += tokens
    return "\n".join(picked)


class SummaryNode:
    """摘要树的一个节点。"""

    __slots__ = ("level", "content", "tokens", "children")

    def __init__(self, level: int, content: str) -> None:
        """初始化节点。

        :param level: 层级（0=原始条目）。
        :param content: 内容。
        """
        self.level: int = level
        self.content: str = content
        self.tokens: int = estimate_tokens(content)
        self.children: list[SummaryNode] = []

    def add_child(self, node: SummaryNode) -> None:
        """添加子节点。"""
        self.children.append(node)


class Summarizer:
    """摘要器 —— 支持确定性抽取与 LLM 回调两种后端。

    :param llm_summarize: 可选 LLM 回调：``(text, target_tokens) -> str``。
        调用失败时自动回退到确定性抽取。
    """

    def __init__(
        self,
        llm_summarize: Callable[[str, int | None], str] | None = None,
    ) -> None:
        """初始化摘要器。"""
        self._llm_summarize: Callable[[str, int | None], str] | None = llm_summarize

    def summarize_text(self, text: str, *, target_tokens: int | None = None) -> str:
        """摘要一段文本。

        :param text: 原文。
        :param target_tokens: 目标 token 数。
        :returns: 摘要。
        """
        if not text:
            return ""
        if self._llm_summarize is not None:
            try:
                result = self._llm_summarize(text, target_tokens)
                if result and result.strip():
                    return result.strip()
            except Exception:  # noqa: BLE001 —— LLM 失败必须兜底，不阻断压缩
                pass
        return summarize_by_extraction(text, target_tokens=target_tokens)

    def summarize_chunks(
        self,
        chunks: Iterable[MemoryChunk],
        *,
        target_tokens: int | None = None,
    ) -> str:
        """摘要一批记忆块。"""
        items = chunks_from(chunks)
        if not items:
            return ""
        # 优先抽取高显著性块的内容。
        ordered = sorted(items, key=lambda item: item.salience, reverse=True)
        text = "\n".join(item.content for item in ordered)
        return self.summarize_text(text, target_tokens=target_tokens)

    def build_summary_tree(
        self,
        chunks: Iterable[MemoryChunk],
        *,
        target_tokens: int | None = None,
        max_level: int = 3,
    ) -> SummaryNode:
        """构建摘要树（level 0..N）。

        :param chunks: 原始条目。
        :param target_tokens: 每层摘要目标 token。
        :param max_level: 最大折叠层数。
        :returns: 根节点（level 0 的虚拟根，children 为原始条目）。
        """
        items = chunks_from(chunks)
        root = SummaryNode(0, "")
        for item in items:
            root.add_child(SummaryNode(0, item.content))
        level = 1
        current = [node for node in root.children]
        while current and level <= max_level:
            text = "\n".join(node.content for node in current)
            summary = self.summarize_text(text, target_tokens=target_tokens)
            node = SummaryNode(level, summary)
            node.children = current
            root.add_child(node)
            current = [node]
            level += 1
        return root

    def to_plain(self, node: SummaryNode) -> str:
        """把摘要树渲染成纯文本（顶层摘要优先）。"""
        if not node.children:
            return node.content
        highest = max(node.children, key=lambda child: child.level)
        return highest.content if highest.content else node.content
