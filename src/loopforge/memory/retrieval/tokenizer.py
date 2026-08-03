"""检索分词器 —— 零依赖、代码感知、中英混排。

来源：``docs/TASK_BREAKDOWN.md`` §7.3.3、``docs/ARCHITECTURE.md`` §7.4。

为什么自己写分词器
------------------
LoopForge 的记忆正文不是自然语言散文，而是「日志 + 报错栈 + 代码片段 + 中文计划」
的混合体。通用中文分词器（jieba 等）会把 ``get_settings`` 切成一个整词、把
``LF-BUDGET-1101`` 切碎、把 ``src/loopforge/core/clock.py`` 当成噪声丢掉——
而这三类恰恰是 Agent 回忆时最需要精确命中的东西。

所以这里手写一套规则，目标是 **召回优先**：同一个标识符尽量产出多个可命中的词元。

切分策略
--------
1. **ASCII 词块**：按非字母数字下划线切开，得到「原子块」。
2. **路径 / 点号**：``src/loopforge/core/clock.py`` 额外产出
   ``src`` ``loopforge`` ``core`` ``clock`` ``py``，同时保留整串。
3. **snake_case**：``get_or_raise`` → 整串 + ``get`` ``or`` ``raise``。
4. **camelCase / PascalCase**：``BudgetGuard`` → 整串 + ``budget`` ``guard``；
   连续大写（``HTTPError``）按「大写串 + 尾随小写」拆成 ``http`` ``error``。
5. **错误码 / 版本号**：``LF-BUDGET-1101`` → 整串 + ``lf`` ``budget`` ``1101``。
6. **CJK**：连续汉字串按 **bigram**（二元组）切分，并保留单字。
   bigram 让「预算超支」能命中「预算」「算超」「超支」，比单字精度高得多，
   又不需要词典，符合「零外部依赖」这条硬约束。
7. **停用词**：中英各一份极小的表，只删真正无信息量的词（``the`` ``的``），
   绝不删 ``not`` ``no`` ``否`` 这类会翻转语义的词。

所有输出统一小写。空白与标点不产出词元。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "CJK_STOPWORDS",
    "EN_STOPWORDS",
    "MAX_TERM_LENGTH",
    "Tokenizer",
    "default_tokenizer",
    "term_frequencies",
    "tokenize",
]

#: 倒排表 ``term`` 列是 ``String(200)``，超长词元必须截断，否则写库直接炸。
MAX_TERM_LENGTH: Final[int] = 200

#: 英文停用词 —— 只收「删掉不影响检索意图」的功能词。
#: 刻意 **不含** ``no`` / ``not`` / ``off`` / ``all``：它们在
#: 「不要删除所有文件」这类约束里承担全部语义。
EN_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
        "for", "from", "had", "has", "have", "he", "her", "his", "in",
        "into", "is", "it", "its", "of", "on", "or", "our", "she", "so",
        "that", "the", "their", "them", "then", "there", "these", "they",
        "this", "those", "to", "was", "were", "will", "with", "would",
    }
)

#: 中文停用词 —— 同样只收纯功能词，保留「不」「否」「必须」等强语义字。
CJK_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "的", "了", "着", "过", "和", "与", "或", "在", "是", "有",
        "把", "被", "给", "对", "从", "到", "于", "而", "则", "也",
        "就", "都", "还", "又", "很", "个", "them", "这", "那", "其",
    }
)

#: 连续 CJK（含中日韩统一表意文字与扩展 A 区）。
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

#: ASCII 原子块：字母数字 + 下划线 + 连字符 + 点号 + 斜杠（保住路径与错误码）。
_ASCII_RUN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_\-./]*")

#: snake / kebab / 路径 / 点号 的通用分隔符。
_HARD_SPLIT = re.compile(r"[_\-./\\]+")

#: camelCase 与连续大写的边界。``HTTPError`` → ``HTTP`` + ``Error``。
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

#: 纯数字块 —— 单独出现时长度 < 2 的直接丢（``1`` ``2`` 无检索价值）。
_PURE_DIGITS = re.compile(r"^\d+$")


def _split_camel(token: str) -> list[str]:
    """把 camelCase / PascalCase 拆成小写子词。

    Args:
        token: 单个 ASCII 原子块，不含分隔符。

    Returns:
        子词列表（已小写）；无驼峰边界时返回空列表。
    """
    parts = [piece for piece in _CAMEL_SPLIT.split(token) if piece]
    if len(parts) <= 1:
        return []
    return [piece.lower() for piece in parts]


def _bigrams(run: str) -> list[str]:
    """把一段连续汉字切成二元组。

    单字长度的串原样返回，保证「不」这种单字约束不会丢失。

    Args:
        run: 连续汉字串。

    Returns:
        bigram 列表。
    """
    if len(run) <= 1:
        return [run]
    return [run[index : index + 2] for index in range(len(run) - 1)]


@dataclass(slots=True)
class Tokenizer:
    """可配置的检索分词器。

    :param lowercase: 是否统一小写。关掉只在调试时有意义。
    :param drop_stopwords: 是否过滤停用词。
    :param cjk_bigram: 中文是否走 bigram；关掉则退化为单字切分。
    :param keep_whole: 是否保留未拆分的原串（``get_or_raise`` 本身）。
    :param min_length: ASCII 词元最短长度，小于此长度直接丢弃。
    :param extra_stopwords: 追加的停用词（调用方场景化补充）。
    """

    lowercase: bool = True
    drop_stopwords: bool = True
    cjk_bigram: bool = True
    keep_whole: bool = True
    min_length: int = 2
    extra_stopwords: frozenset[str] = field(default_factory=frozenset)

    def _stopwords(self) -> frozenset[str]:
        """当前生效的停用词全集。"""
        if not self.drop_stopwords:
            return frozenset()
        return EN_STOPWORDS | CJK_STOPWORDS | self.extra_stopwords

    def _accept(self, term: str, stopwords: frozenset[str]) -> bool:
        """判断一个候选词元是否入表。

        Args:
            term: 候选词元（已小写）。
            stopwords: 生效的停用词集合。

        Returns:
            是否保留。
        """
        if not term:
            return False
        if term in stopwords:
            return False
        # CJK 词元长度 1~2 就够信息量，不受 min_length 约束。
        if _CJK_RUN.fullmatch(term):
            return True
        if len(term) < self.min_length:
            return False
        if _PURE_DIGITS.fullmatch(term) and len(term) < 3:
            return False
        return True

    def _expand_ascii(self, raw: str) -> Iterator[str]:
        """把一个 ASCII 原子块展开成多个候选词元。

        Args:
            raw: 形如 ``src/loopforge/core/clock.py`` 的原子块。

        Yields:
            候选词元（尚未过滤，未统一小写）。
        """
        cleaned = raw.strip("._-/\\")
        if not cleaned:
            return
        if self.keep_whole:
            yield cleaned
        pieces = [piece for piece in _HARD_SPLIT.split(cleaned) if piece]
        if len(pieces) > 1:
            yield from pieces
        for piece in pieces or [cleaned]:
            yield from _split_camel(piece)

    def tokenize(self, text: str) -> list[str]:
        """切词主入口。

        Args:
            text: 任意混排文本。

        Returns:
            **有序、可重复** 的词元列表（顺序即位置，BM25 需要词频）。
        """
        if not text:
            return []
        stopwords = self._stopwords()
        out: list[str] = []
        seen_positions: set[tuple[str, int]] = set()
        cursor = 0

        for match in _ASCII_RUN.finditer(text):
            # 先处理两个 ASCII 块之间的 CJK 片段，保证词元顺序即原文顺序。
            gap = text[cursor : match.start()]
            self._emit_cjk(gap, stopwords, out)
            cursor = match.end()
            position = match.start()
            for candidate in self._expand_ascii(match.group(0)):
                term = candidate.lower() if self.lowercase else candidate
                term = term[:MAX_TERM_LENGTH]
                if not self._accept(term, stopwords):
                    continue
                # 同一位置展开出的重复词元只留一个（``py`` 与 ``py`` ）。
                key = (term, position)
                if key in seen_positions:
                    continue
                seen_positions.add(key)
                out.append(term)

        self._emit_cjk(text[cursor:], stopwords, out)
        return out

    def _emit_cjk(self, chunk: str, stopwords: frozenset[str], out: list[str]) -> None:
        """把一段可能含汉字的文本切成词元并追加到 ``out``。

        Args:
            chunk: 待处理片段。
            stopwords: 生效停用词。
            out: 输出列表，原地追加。
        """
        if not chunk:
            return
        for run_match in _CJK_RUN.finditer(chunk):
            run = run_match.group(0)
            grams = _bigrams(run) if self.cjk_bigram else list(run)
            for gram in grams:
                if self._accept(gram, stopwords):
                    out.append(gram[:MAX_TERM_LENGTH])

    def unique_terms(self, text: str) -> list[str]:
        """切词并去重，保持首次出现顺序（查询侧用）。

        Args:
            text: 查询串。

        Returns:
            去重后的词元列表。
        """
        seen: set[str] = set()
        result: list[str] = []
        for term in self.tokenize(text):
            if term not in seen:
                seen.add(term)
                result.append(term)
        return result

    def frequencies(self, text: str) -> dict[str, int]:
        """统计词频。

        Args:
            text: 文档正文。

        Returns:
            ``{词元: 出现次数}``。
        """
        table: dict[str, int] = {}
        for term in self.tokenize(text):
            table[term] = table.get(term, 0) + 1
        return table

    def positions(self, text: str) -> dict[str, list[int]]:
        """统计每个词元的出现下标（写倒排表的 ``positions`` 列）。

        Args:
            text: 文档正文。

        Returns:
            ``{词元: [下标, ...]}``，下标是词元序列中的序号，不是字符偏移。
        """
        table: dict[str, list[int]] = {}
        for index, term in enumerate(self.tokenize(text)):
            table.setdefault(term, []).append(index)
        return table


#: 进程级共享的默认分词器（无状态，可安全复用）。
_DEFAULT = Tokenizer()


def default_tokenizer() -> Tokenizer:
    """取默认分词器实例。

    Returns:
        无状态、可共享的 :class:`Tokenizer`。
    """
    return _DEFAULT


def tokenize(text: str) -> list[str]:
    """用默认分词器切词的便捷函数。

    Args:
        text: 待切分文本。

    Returns:
        词元列表。
    """
    return _DEFAULT.tokenize(text)


def term_frequencies(texts: Iterable[str] | Sequence[str]) -> dict[str, int]:
    """统计一批文档的全局词频（构建索引前的探查工具）。

    Args:
        texts: 文档正文序列。

    Returns:
        ``{词元: 全局出现次数}``。
    """
    total: dict[str, int] = {}
    for text in texts:
        for term, count in _DEFAULT.frequencies(text).items():
            total[term] = total.get(term, 0) + count
    return total
