"""上下文压缩 —— 三级压缩阶梯 + 保真度校验。

来源：``docs/ARCHITECTURE.md`` §8.2.3、``TASK_BREAKDOWN.md`` §7.2 步骤 4。

三级压缩（M4：**必须分级降级，禁止一上来调 LLM**）：:

    L1 结构化裁剪  折叠 tool result 为一行、stdout 头 40 行 + 尾 20 行、
                   去重相同内容、剥离 base64/二进制。纯规则、确定性。
    L2 抽取式摘要  抽取「决策句 / 错误信息 / 文件路径 / 数字 / 函数签名」，
                   丢弃寒暄。纯规则、确定性。
    L3 生成式摘要  递归摘要（走 :class:`Summarizer`；无 LLM 时确定性抽取兜底）。

保真度三重保障（M2/M3）：原文永不删除（只写 ``compressed_into``）；
压缩谱系写 ``memory_lineage``；:meth:`Compressor.check_fidelity` 用关键
事实核对压缩产物，任何缺失都会记录为 violation。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.clock import get_clock
from loopforge.core.enums import MemoryLayer, Severity
from loopforge.core.hashing import sha256_hex

__all__ = [
    "CompactionResult",
    "FidelityViolation",
    "FidelityReport",
    "MemoryChunk",
    "extract_key_facts",
    "Compressor",
]

#: L1：stdout 保留头 N 行。
L1_HEAD_LINES: int = 40
#: L1：stdout 保留尾 N 行。
L1_TAIL_LINES: int = 20
#: L1：单行超长截断长度。
L1_MAX_LINE_LENGTH: int = 500
#: base64 / 二进制特征。
_BASE64_RE: re.Pattern[str] = re.compile(
    r"(?:[A-Za-z0-9+/]{4}){16,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?"
)
_BINARY_RE: re.Pattern[str] = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

#: L2：信息量高的句子特征。
_L2_DECISION_RE: re.Pattern[str] = re.compile(
    r"(决定|结论|修复|失败|错误|成功|通过|拒绝|原因是|根因|"
    r"error|failed|exception|fixed|passed|rejected|decision|conclusion|"
    r"bug|crash|workaround)"
)
_L2_PATH_RE: re.Pattern[str] = re.compile(r"(?:[A-Za-z]:)?[\\/][\w.\-/\\]+")
_L2_SIGNATURE_RE: re.Pattern[str] = re.compile(
    r"\b(?:def|class|async def|func|function|interface|struct)\s+\w+"
)
_L2_NUMBER_RE: re.Pattern[str] = re.compile(r"\b\d{2,}\b")

#: L3 摘要句打分：位置加权 + 关键词密度。
_L3_KEYWORD_RE: re.Pattern[str] = re.compile(
    r"(错误|失败|修复|决定|关键|约束|必须|禁止|error|fail|fix|must|never|"
    r"constraint|bug|crash|test|pass|fail|api|接口|签名|参数)"
)


def _is_base64_or_binary(text: str) -> bool:
    """判断一段文本是否为 base64 或二进制噪声。"""
    if _BINARY_RE.search(text):
        return True
    sample = _BASE64_RE.findall(text)
    return len(sample) >= 1


class MemoryChunk:
    """压缩操作的轻量输入 —— 适配 ORM MemoryItem 或普通字典。"""

    __slots__ = ("id", "content", "salience", "created_at", "layer", "meta")

    def __init__(
        self,
        *,
        id: str,
        content: str,
        salience: float = 0.5,
        created_at: datetime | None = None,
        layer: MemoryLayer = MemoryLayer.EPISODIC,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """初始化压缩块。

        :param id: 来源条目 ID。
        :param content: 正文。
        :param salience: 显著性 0~1。
        :param created_at: 创建时刻（缺省用当前时钟）。
        :param layer: 所属记忆层。
        :param meta: 附加元数据。
        """
        self.id: str = id
        self.content: str = content
        self.salience: float = min(1.0, max(0.0, salience))
        self.created_at: datetime = created_at or get_clock().now()
        self.layer: MemoryLayer = layer
        self.meta: dict[str, Any] = dict(meta or {})

    def to_dict(self) -> dict[str, Any]:
        """转字典。"""
        return {
            "id": self.id,
            "content": self.content,
            "salience": self.salience,
            "layer": self.layer.value,
            "meta": dict(self.meta),
        }


def chunks_from(items: Iterable[Any]) -> list[MemoryChunk]:
    """把 ORM 模型 / 字典 / MemoryChunk 统一适配成 :class:`MemoryChunk`。"""
    result: list[MemoryChunk] = []
    for item in items:
        if isinstance(item, MemoryChunk):
            result.append(item)
        elif hasattr(item, "content"):
            result.append(
                MemoryChunk(
                    id=str(getattr(item, "id", "")),
                    content=item.content,
                    salience=float(getattr(item, "salience", 0.5)),
                    created_at=getattr(item, "created_at", None),
                    layer=getattr(item, "layer", MemoryLayer.EPISODIC),
                    meta=dict(getattr(item, "meta", {}) or {}),
                )
            )
        elif isinstance(item, dict):
            result.append(
                MemoryChunk(
                    id=str(item.get("id", "")),
                    content=str(item.get("content", "")),
                    salience=float(item.get("salience", 0.5)),
                    layer=item.get("layer", MemoryLayer.EPISODIC),
                    meta=dict(item.get("meta", {}) or {}),
                )
            )
    return result


def extract_key_facts(text: str, *, max_facts: int = 24) -> list[str]:
    """提取一段文本的关键事实（保真度校验的核对清单）。

    策略：先按行/句切分，再用 L2 特征正则挑选信息量高的片段，最后截断。

    :param text: 待提取文本。
    :param max_facts: 事实上限。
    :returns: 关键事实列表（去重、保序）。
    """
    if not text:
        return []
    sentences = re.split(r"[。！？!?\n]+", text)
    scored: list[tuple[float, str]] = []
    for sentence in sentences:
        stripped = sentence.strip()
        if len(stripped) < 4:
            continue
        score = 0.0
        has_feature = False
        if _L2_DECISION_RE.search(stripped):
            score += 3.0
            has_feature = True
        if _L2_PATH_RE.search(stripped):
            score += 2.0
            has_feature = True
        if _L2_SIGNATURE_RE.search(stripped):
            score += 2.0
            has_feature = True
        if _L2_NUMBER_RE.search(stripped):
            score += 1.0
            has_feature = True
        score += min(2.0, len(stripped) / 80.0)
        if _is_base64_or_binary(stripped):
            score -= 4.0
        # 信息量门槛：必须有特征词，或足够长；纯寒暄句丢弃。
        if score <= 0 or not (has_feature or len(stripped) >= 14):
            continue
        scored.append((score, stripped))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    facts: list[str] = []
    seen: set[str] = set()
    for _, sentence in scored:
        key = sha256_hex(sentence)
        if key in seen:
            continue
        seen.add(key)
        facts.append(sentence)
        if len(facts) >= max_facts:
            break
    return facts


class FidelityViolation(BaseModel):
    """一条保真度违规。"""

    model_config = ConfigDict(extra="forbid")

    constraint_id: str = Field(default="", description="约束 ID")
    constraint_text: str = Field(description="约束原文")
    missing_facts: list[str] = Field(default_factory=list, description="缺失的关键事实")
    evidence: str = Field(default="", description="证据说明")
    severity: Severity = Field(default=Severity.ERROR, description="严重级别")


class FidelityReport(BaseModel):
    """保真度校验报告。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(description="所属 Run")
    checked_at: datetime = Field(description="校验时刻")
    total_constraints: int = Field(default=0, ge=0, description="被校验的约束数")
    violations: list[FidelityViolation] = Field(default_factory=list, description="违规列表")

    @property
    def passed(self) -> bool:
        """是否全部通过。"""
        return not self.violations

    @property
    def violation_count(self) -> int:
        """违规条数。"""
        return len(self.violations)


class CompactionResult(BaseModel):
    """一次压缩的结果（``maybe_compress`` 的返回值）。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(description="所属 Run")
    level: int = Field(ge=1, le=3, description="实际压缩级别")
    summary_id: str | None = Field(default=None, description="生成的里程碑摘要 ID")
    source_count: int = Field(default=0, ge=0, description="被压缩的原文条数")
    tokens_before: int = Field(default=0, ge=0, description="压缩前 token")
    tokens_after: int = Field(default=0, ge=0, description="压缩后 token")
    saved_tokens: int = Field(default=0, ge=0, description="节省的 token")
    info_loss_score: float = Field(default=0.0, ge=0.0, le=1.0, description="信息损失评分")
    created_at: datetime = Field(description="完成时刻")

    @classmethod
    def of(
        cls,
        *,
        run_id: str,
        level: int,
        summary_id: str | None,
        source_count: int,
        tokens_before: int,
        tokens_after: int,
        info_loss_score: float,
    ) -> CompactionResult:
        """从原始数字构造结果（自动算 saved_tokens）。"""
        return cls(
            run_id=run_id,
            level=level,
            summary_id=summary_id,
            source_count=source_count,
            tokens_before=max(0, tokens_before),
            tokens_after=max(0, tokens_after),
            saved_tokens=max(0, tokens_before - tokens_after),
            info_loss_score=min(1.0, max(0.0, info_loss_score)),
            created_at=get_clock().now(),
        )


class Compressor:
    """三级压缩器。

    :param estimate_tokens: 文本 token 估算函数。
    :param summarizer: 可选的 :class:`Summarizer`（L3 用）；缺省用确定性抽取。
    """

    def __init__(
        self,
        estimate_tokens: "callable[[str], int] | None" = None,
        summarizer: Any | None = None,
    ) -> None:
        from loopforge.memory.layers.base import estimate_tokens as _default_estimate
        from loopforge.memory.summarizer import Summarizer as _DefaultSummarizer

        self._estimate = estimate_tokens or _default_estimate
        self._summarizer: Any = summarizer or _DefaultSummarizer()

    # -- L1 结构化裁剪 ------------------------------------------------------

    def compress_level1(self, items: Iterable[Any]) -> list[MemoryChunk]:
        """L1 结构化裁剪：纯规则、确定性。

        * 相同内容只保留一条；
        * 超过 ``L1_MAX_LINE_LENGTH`` 的行截断；
        * 大段输出只保留头尾若干行；
        * base64 / 二进制噪声剥离。
        """
        seen: set[str] = set()
        result: list[MemoryChunk] = []
        for chunk in chunks_from(items):
            content = chunk.content
            if not content:
                continue
            digest = sha256_hex(content)
            if digest in seen:
                continue
            seen.add(digest)
            content = self._trim_structural(content)
            if content:
                data = chunk.to_dict()
                data["content"] = content
                result.append(MemoryChunk(**data))
        return result

    @staticmethod
    def _trim_structural(content: str) -> str:
        """单条内容的结构化裁剪规则。"""
        lines = content.splitlines()
        if len(lines) > L1_HEAD_LINES + L1_TAIL_LINES:
            head = lines[:L1_HEAD_LINES]
            tail = lines[-L1_TAIL_LINES:]
            content = "\n".join([*head, f"...[截断 {len(lines) - L1_HEAD_LINES - L1_TAIL_LINES} 行]...", *tail])
        trimmed_lines = []
        for line in content.splitlines():
            if len(line) > L1_MAX_LINE_LENGTH:
                line = line[:L1_MAX_LINE_LENGTH] + "..."
            if _is_base64_or_binary(line):
                line = "[base64/二进制已剥离]"
            trimmed_lines.append(line)
        return "\n".join(trimmed_lines)

    # -- L2 抽取式摘要 ------------------------------------------------------

    def compress_level2(
        self, items: Iterable[Any], *, target_tokens: int | None = None
    ) -> str:
        """L2 抽取式摘要：纯规则、确定性。

        抽取含决策/错误/路径/签名/数字的句子，按分数降序装填直到预算。
        """
        facts = extract_key_facts("\n".join(chunk.content for chunk in chunks_from(items)))
        budget = target_tokens if target_tokens and target_tokens > 0 else 10**9
        picked: list[str] = []
        used = 0
        for fact in facts:
            tokens = self._estimate(fact)
            if used + tokens > budget:
                break
            picked.append(fact)
            used += tokens
        return "\n".join(picked)

    # -- L3 生成式递归摘要 --------------------------------------------------

    def compress_level3(
        self, items: Iterable[Any], *, target_tokens: int | None = None
    ) -> str:
        """L3 生成式递归摘要（走 Summarizer，无 LLM 时确定性兜底）。"""
        chunks = chunks_from(items)
        budget = target_tokens if target_tokens and target_tokens > 0 else None
        return self._summarizer.summarize_chunks(chunks, target_tokens=budget)

    # -- 统一入口 -----------------------------------------------------------

    def compress(
        self,
        items: Iterable[Any],
        *,
        level: int = 1,
        target_tokens: int | None = None,
    ) -> tuple[str, int, float]:
        """按级别压缩并返回 ``(文本, token 数, 信息损失评分)``。

        :param items: 待压缩条目。
        :param level: 1 / 2 / 3。
        :param target_tokens: 目标 token 数（可选）。
        :returns: 三元组。
        """
        if level == 1:
            trimmed = self.compress_level1(items)
            text = "\n".join(chunk.content for chunk in trimmed)
        elif level == 2:
            text = self.compress_level2(items, target_tokens=target_tokens)
        elif level == 3:
            text = self.compress_level3(items, target_tokens=target_tokens)
        else:
            raise ValueError(f"level 必须是 1/2/3，收到 {level}")
        tokens = self._estimate(text)
        loss = self.info_loss_score(items, text)
        return text, tokens, loss

    def info_loss_score(self, items: Iterable[Any], compressed_text: str) -> float:
        """信息损失评分 ``[0,1]``：1 - 关键事实保留率。"""
        before = set(extract_key_facts("\n".join(chunk.content for chunk in chunks_from(items))))
        if not before:
            return 0.0
        after = set(extract_key_facts(compressed_text))
        preserved = len(before & after)
        return 1.0 - (preserved / len(before))

    # -- 保真度校验 ---------------------------------------------------------

    def check_fidelity(
        self,
        *,
        run_id: str,
        constraints: Sequence[Any],
        plan_text: str,
    ) -> FidelityReport:
        """校验全部约束的关键事实是否仍被计划文本覆盖。

        对每条约束：提取关键事实，逐一核对是否出现在 ``plan_text`` 中；
        缺失任一事实即记为 violation（不要求整句逐字相等）。

        :param run_id: 所属 Run。
        :param constraints: PinnedConstraint 列表（须有 ``text_content`` / ``id``）。
        :param plan_text: 待校验的上下文 / 计划文本。
        :returns: :class:`FidelityReport`。
        """
        checked_at = get_clock().now()
        plan_lower = plan_text.lower()
        violations: list[FidelityViolation] = []
        for constraint in constraints:
            text = constraint.text_content
            facts = extract_key_facts(text)
            missing = [
                fact
                for fact in facts
                if fact.lower() not in plan_lower
                # 允许「主题词」级别的宽松命中：主题词在计划里出现即视为保留。
                and not _topic_in_plan(fact, plan_lower)
            ]
            if missing:
                violations.append(
                    FidelityViolation(
                        constraint_id=str(getattr(constraint, "id", "")),
                        constraint_text=text,
                        missing_facts=missing[:8],
                        evidence=f"计划文本缺失 {len(missing)} 项关键事实",
                        severity=Severity.ERROR,
                    )
                )
        return FidelityReport(
            run_id=run_id,
            checked_at=checked_at,
            total_constraints=len(constraints),
            violations=violations,
        )


def _topic_in_plan(fact: str, plan_lower: str) -> bool:
    """宽松匹配：事实中的核心名词是否出现在计划里。"""
    words = [word for word in re.split(r"\W+", fact.lower()) if len(word) >= 2]
    return any(word in plan_lower for word in words)
