"""记忆域 DTO —— 分层记忆、里程碑摘要、钉死约束与检索。

设计要点
--------

1. **写入侧只接受「文本 + 归属」**，token 数、salience 由服务层计算后覆盖，
   避免调用方伪造 salience 把垃圾记忆顶到检索首位。
2. **检索请求显式声明层过滤**，默认不含 ``SEMANTIC``（跨 Run 语义层需显式开启，
   防止无意识的跨项目信息泄漏）。
3. ``PinnedConstraint`` 的 ``text_hash`` 由服务层算，DTO 不接受外部传入。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from loopforge.core.enums import MemoryLayer
from loopforge.schemas.common import LFModel, ORMModel

__all__ = [
    "DEFAULT_SEARCH_LAYERS",
    "MEMORY_ORIGINS",
    "MemoryItemCreate",
    "MemoryItemRead",
    "MemoryQuery",
    "MemorySearchHit",
    "MemorySearchRequest",
    "MemorySearchResponse",
    "MilestoneSummaryCreate",
    "MilestoneSummaryRead",
    "PinnedConstraintCreate",
    "PinnedConstraintRead",
    "ContextAssembleRequest",
    "ContextBlock",
    "ContextAssembleResponse",
]

#: 检索默认覆盖的记忆层（不含 SEMANTIC，跨 Run 需显式指定）。
DEFAULT_SEARCH_LAYERS: tuple[MemoryLayer, ...] = (
    MemoryLayer.PINNED,
    MemoryLayer.WORKING,
    MemoryLayer.EPISODIC,
    MemoryLayer.MILESTONE,
)

#: 钉死约束的合法来源。
MEMORY_ORIGINS: tuple[str, ...] = ("user", "policy", "reviewer", "system", "postmortem")


class MemoryItemCreate(LFModel):
    """写入一条记忆。"""

    run_id: str = Field(min_length=1, max_length=26, description="所属 Run")
    task_id: str | None = Field(default=None, max_length=26, description="所属 Task（可空）")
    layer: MemoryLayer = Field(default=MemoryLayer.WORKING, description="记忆层")
    content: str = Field(min_length=1, max_length=200_000, description="记忆正文")
    meta: dict[str, object] = Field(default_factory=dict, description="附加元数据")

    @field_validator("content")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        """拒绝纯空白正文。"""
        if not value.strip():
            raise ValueError("记忆正文不能为空白")
        return value


class MemoryItemRead(ORMModel):
    """记忆条目读模型。"""

    id: str = Field(description="记忆 ID")
    run_id: str = Field(description="所属 Run")
    task_id: str | None = Field(default=None, description="所属 Task")
    layer: MemoryLayer = Field(description="记忆层")
    content: str = Field(description="记忆正文")
    tokens: int = Field(default=0, ge=0, description="token 估算")
    salience: float = Field(default=0.0, description="显著度")
    compressed_into: str | None = Field(default=None, description="被压缩进的摘要 ID")
    meta: dict[str, object] = Field(default_factory=dict, description="附加元数据")
    created_at: datetime = Field(description="创建时间")

    @property
    def is_compressed(self) -> bool:
        """是否已被压缩进某条里程碑摘要。"""
        return self.compressed_into is not None


class MemoryQuery(LFModel):
    """按条件列举记忆（非全文检索）。"""

    run_id: str | None = Field(default=None, max_length=26, description="按 Run 过滤")
    task_id: str | None = Field(default=None, max_length=26, description="按 Task 过滤")
    layer: MemoryLayer | None = Field(default=None, description="按层过滤")
    include_compressed: bool = Field(default=False, description="是否包含已压缩条目")
    min_salience: float | None = Field(default=None, ge=0.0, le=1.0, description="显著度下限")


class MemorySearchRequest(LFModel):
    """混合检索请求（BM25 + 向量 + 时间衰减，由服务层融合）。"""

    run_id: str = Field(min_length=1, max_length=26, description="检索范围 Run")
    query: str = Field(min_length=1, max_length=4_000, description="查询串")
    layers: tuple[MemoryLayer, ...] = Field(
        default=DEFAULT_SEARCH_LAYERS, description="参与检索的记忆层"
    )
    top_k: int = Field(default=10, ge=1, le=100, description="返回条数")
    token_budget: int | None = Field(
        default=None, ge=1, le=1_000_000, description="结果总 token 上限"
    )
    include_pinned: bool = Field(default=True, description="是否强制拼上钉死约束")

    @field_validator("layers")
    @classmethod
    def _dedupe_layers(cls, value: tuple[MemoryLayer, ...]) -> tuple[MemoryLayer, ...]:
        """去重且至少保留一层。"""
        seen: list[MemoryLayer] = []
        for layer in value:
            if layer not in seen:
                seen.append(layer)
        if not seen:
            raise ValueError("layers 至少需要一层")
        return tuple(seen)


class MemorySearchHit(ORMModel):
    """一条检索命中。"""

    item: MemoryItemRead = Field(description="命中的记忆")
    score: float = Field(description="融合得分")
    bm25_score: float = Field(default=0.0, description="BM25 分量")
    vector_score: float = Field(default=0.0, description="向量分量")
    recency_score: float = Field(default=0.0, description="时间衰减分量")


class MemorySearchResponse(LFModel):
    """检索结果集合。"""

    hits: list[MemorySearchHit] = Field(default_factory=list, description="命中列表")
    total_tokens: int = Field(default=0, ge=0, description="命中总 token")
    truncated: bool = Field(default=False, description="是否因 token 预算被截断")

    @property
    def hit_count(self) -> int:
        """命中条数。"""
        return len(self.hits)


class MilestoneSummaryCreate(LFModel):
    """写入一条里程碑摘要。"""

    run_id: str = Field(min_length=1, max_length=26, description="所属 Run")
    level: int = Field(default=1, ge=1, le=3, description="摘要层级：1/2/3")
    covers_from_seq: int = Field(default=0, ge=0, description="覆盖事件序号起点")
    covers_to_seq: int = Field(default=0, ge=0, description="覆盖事件序号终点")
    content: str = Field(min_length=1, max_length=100_000, description="摘要正文")
    source_item_ids: list[str] = Field(default_factory=list, description="来源记忆 ID 列表")

    @model_validator(mode="after")
    def _check_range(self) -> MilestoneSummaryCreate:
        """覆盖区间必须非逆序。"""
        if self.covers_to_seq < self.covers_from_seq:
            raise ValueError("covers_to_seq 不能小于 covers_from_seq")
        return self


class MilestoneSummaryRead(ORMModel):
    """里程碑摘要读模型。"""

    id: str = Field(description="摘要 ID")
    run_id: str = Field(description="所属 Run")
    level: int = Field(description="摘要层级")
    covers_from_seq: int = Field(default=0, description="覆盖起点")
    covers_to_seq: int = Field(default=0, description="覆盖终点")
    content: str = Field(description="摘要正文")
    tokens: int = Field(default=0, ge=0, description="token 估算")
    source_item_ids: list[str] = Field(default_factory=list, description="来源记忆 ID")
    created_at: datetime = Field(description="创建时间")

    @property
    def covered_events(self) -> int:
        """覆盖的事件条数。"""
        return max(0, self.covers_to_seq - self.covers_from_seq + 1)


class PinnedConstraintCreate(LFModel):
    """钉死一条永不遗忘的约束。"""

    run_id: str = Field(min_length=1, max_length=26, description="所属 Run")
    text_content: str = Field(min_length=1, max_length=4_000, description="约束正文")
    priority: int = Field(default=100, ge=0, le=1000, description="优先级，越小越靠前")
    origin: str = Field(default="user", max_length=32, description="来源")
    origin_step_id: str | None = Field(default=None, max_length=26, description="来源 Step")

    @field_validator("origin")
    @classmethod
    def _check_origin(cls, value: str) -> str:
        """来源必须在白名单内。"""
        normalized = value.strip().lower()
        if normalized not in MEMORY_ORIGINS:
            raise ValueError(f"origin 必须是 {MEMORY_ORIGINS} 之一，收到 {value!r}")
        return normalized


class PinnedConstraintRead(ORMModel):
    """钉死约束读模型。"""

    id: str = Field(description="约束 ID")
    run_id: str = Field(description="所属 Run")
    text_content: str = Field(description="约束正文")
    text_hash: str = Field(description="正文哈希（去重用）")
    priority: int = Field(default=100, description="优先级")
    origin: str = Field(default="user", description="来源")
    origin_step_id: str | None = Field(default=None, description="来源 Step")
    hit_count: int = Field(default=0, ge=0, description="被注入上下文的次数")
    active: bool = Field(default=True, description="是否生效")
    created_at: datetime = Field(description="创建时间")


class ContextAssembleRequest(LFModel):
    """上下文装配请求 —— 把记忆压进一个 token 预算里。"""

    run_id: str = Field(min_length=1, max_length=26, description="所属 Run")
    task_id: str | None = Field(default=None, max_length=26, description="当前 Task")
    query: str = Field(default="", max_length=4_000, description="用于检索的查询串")
    token_budget: int = Field(default=8_000, ge=256, le=1_000_000, description="总 token 预算")
    reserve_for_output: int = Field(
        default=1_024, ge=0, le=100_000, description="为输出保留的 token"
    )

    @model_validator(mode="after")
    def _check_budget(self) -> ContextAssembleRequest:
        """输出保留量不能吃掉整个预算。"""
        if self.reserve_for_output >= self.token_budget:
            raise ValueError("reserve_for_output 必须小于 token_budget")
        return self

    @property
    def input_budget(self) -> int:
        """可用于输入的 token 预算。"""
        return self.token_budget - self.reserve_for_output


class ContextBlock(LFModel):
    """装配后的一个上下文块。"""

    source: str = Field(description="来源标识：pinned/milestone/memory/task")
    layer: MemoryLayer | None = Field(default=None, description="对应记忆层")
    content: str = Field(description="块正文")
    tokens: int = Field(default=0, ge=0, description="块 token 数")
    ref_id: str | None = Field(default=None, description="来源实体 ID")


class ContextAssembleResponse(LFModel):
    """装配结果。"""

    blocks: list[ContextBlock] = Field(default_factory=list, description="上下文块（有序）")
    total_tokens: int = Field(default=0, ge=0, description="总 token")
    dropped_count: int = Field(default=0, ge=0, description="因预算被丢弃的候选数")
    used_layers: list[MemoryLayer] = Field(default_factory=list, description="实际用到的层")

    def render(self, separator: str = "\n\n") -> str:
        """把所有块拼成一段纯文本提示词片段。"""
        return separator.join(block.content for block in self.blocks)
