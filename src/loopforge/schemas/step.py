"""Step / Checkpoint / 幂等记录 DTO。

对应模型：:mod:`loopforge.models.step`、:mod:`loopforge.models.call`。

幂等三段式在 DTO 上的体现：

* :class:`StepIntent` —— 「我打算做什么」，落库即 ``INTENT_RECORDED``；
* :class:`StepConfirm` —— 「我做完了，结果是这个」，落库即 ``CONFIRMED``；
* :class:`StepFailure` —— 「我失败了」/「我不确定」。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from loopforge.core.enums import PolicyDecision, RoleName, SideEffectClass, StepState
from loopforge.schemas.common import LFModel, ORMModel, PageQuery

__all__ = [
    "STEP_PHASES",
    "CHECKPOINT_KINDS",
    "StepIntent",
    "StepConfirm",
    "StepFailure",
    "StepRead",
    "StepDetail",
    "StepQuery",
    "CheckpointCreate",
    "CheckpointRead",
    "IdempotencyRecordRead",
    "LlmCallRead",
    "ToolCallRead",
]

#: Step 的阶段取值。新增阶段需要同步 WP3 的编排循环。
STEP_PHASES: tuple[str, ...] = (
    "plan",
    "code",
    "review",
    "test",
    "summarize",
    "vcs",
    "recover",
    "tool",
)

#: Checkpoint 的粒度。
CHECKPOINT_KINDS: tuple[str, ...] = ("step", "loop", "milestone")


class StepIntent(LFModel):
    """记录执行意图（幂等三段式第一段）。"""

    run_id: str = Field(min_length=1, max_length=26)
    task_id: str | None = Field(default=None, max_length=26)
    loop_index: int = Field(default=0, ge=0)
    phase: str = Field(min_length=1, max_length=64)
    role: RoleName | None = Field(default=None)
    idempotency_key: str = Field(
        min_length=8, max_length=64, description="全局唯一，由 core.hashing.idempotency_key 生成"
    )
    side_effect_class: SideEffectClass = Field(default=SideEffectClass.PURE)
    intent_payload: dict[str, Any] = Field(
        default_factory=dict, description="打算做什么（调用方必须先脱敏）"
    )
    fence_token: int = Field(default=0, ge=0)
    attempt: int = Field(default=0, ge=0)

    @field_validator("phase")
    @classmethod
    def _check_phase(cls, value: str) -> str:
        """校验阶段取值。

        :raises ValueError: 阶段未知。
        """
        normalized = value.strip().lower()
        if normalized not in STEP_PHASES:
            raise ValueError(f"phase 必须是 {list(STEP_PHASES)} 之一")
        return normalized

    @model_validator(mode="after")
    def _check_unsafe_needs_task(self) -> Self:
        """UNSAFE 副作用必须挂在具体 Task 上。

        没有 Task 归属的不可逆副作用一旦出问题，恢复流程连该找谁问都不知道。

        :raises ValueError: UNSAFE 但缺 ``task_id``。
        """
        if self.side_effect_class is SideEffectClass.UNSAFE and not self.task_id:
            raise ValueError("side_effect_class=UNSAFE 时必须提供 task_id")
        return self


class StepConfirm(LFModel):
    """确认执行结果（幂等三段式第二段）。"""

    result_payload: dict[str, Any] = Field(default_factory=dict)
    external_ref: str | None = Field(
        default=None, max_length=500, description="外部副作用句柄：PR 号 / commit sha"
    )


class StepFailure(LFModel):
    """标记失败或不确定（幂等三段式第三段）。"""

    error_code: str = Field(min_length=1, max_length=32)
    message: str = Field(default="", max_length=4000)
    uncertain: bool = Field(
        default=False, description="True → UNCERTAIN（结果未知，需探测）；False → FAILED"
    )
    result_payload: dict[str, Any] = Field(default_factory=dict)


class StepRead(ORMModel):
    """Step 读模型。"""

    id: str
    run_id: str
    task_id: str | None = None
    loop_index: int = 0
    phase: str
    role: RoleName | None = None
    state: StepState = StepState.PENDING
    idempotency_key: str
    side_effect_class: SideEffectClass = SideEffectClass.PURE
    intent_payload: dict[str, Any] = Field(default_factory=dict)
    result_payload: dict[str, Any] = Field(default_factory=dict)
    external_ref: str | None = None
    error_code: str | None = None
    attempt: int = 0
    fence_token: int = 0
    intent_at: datetime | None = None
    confirmed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def is_unconfirmed(self) -> bool:
        """意图已记录但结果未知。"""
        return self.state is StepState.INTENT_RECORDED

    @property
    def needs_manual_review(self) -> bool:
        """UNSAFE 且未确认 —— 必须转人工。"""
        return self.is_unconfirmed and self.side_effect_class is SideEffectClass.UNSAFE


class LlmCallRead(ORMModel):
    """LLM 调用读模型（请求 / 响应体已脱敏）。"""

    id: str
    step_id: str | None = None
    provider: str
    model: str
    role: RoleName | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: str = Field(default="0", description="十进制字符串")
    latency_ms: int = 0
    attempt: int = 0
    failover_from: str | None = None
    finish_reason: str | None = None
    prompt_version: str | None = None
    error_code: str | None = None
    created_at: datetime

    @property
    def total_tokens(self) -> int:
        """输入 + 输出 token 总数。"""
        return self.prompt_tokens + self.completion_tokens


class ToolCallRead(ORMModel):
    """工具调用读模型。"""

    id: str
    step_id: str | None = None
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    exit_code: int | None = None
    duration_ms: int = 0
    policy_decision: PolicyDecision = PolicyDecision.ALLOW
    truncated: bool = False
    bytes_out: int = 0
    error_code: str | None = None
    stderr_excerpt: str | None = None
    created_at: datetime

    @property
    def succeeded(self) -> bool:
        """退出码为 0 且无错误码即视为成功。"""
        return self.exit_code == 0 and self.error_code is None


class StepDetail(StepRead):
    """Step 详情（带 LLM 与工具调用）。"""

    llm_calls: list[LlmCallRead] = Field(default_factory=list)
    tool_calls: list[ToolCallRead] = Field(default_factory=list)


class StepQuery(PageQuery):
    """Step 列表查询参数。"""

    run_id: str | None = Field(default=None, max_length=26)
    task_id: str | None = Field(default=None, max_length=26)
    state: StepState | None = Field(default=None)
    phase: str | None = Field(default=None, max_length=64)
    unconfirmed_only: bool = Field(default=False)


class CheckpointCreate(LFModel):
    """创建检查点。"""

    run_id: str = Field(min_length=1, max_length=26)
    task_id: str | None = Field(default=None, max_length=26)
    loop_index: int = Field(default=0, ge=0)
    kind: str = Field(default="loop")
    snapshot: dict[str, Any] = Field(default_factory=dict)
    last_step_id: str | None = Field(default=None, max_length=26)
    last_event_seq: int = Field(default=0, ge=0)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        """校验检查点粒度。

        :raises ValueError: 粒度未知。
        """
        normalized = value.strip().lower()
        if normalized not in CHECKPOINT_KINDS:
            raise ValueError(f"kind 必须是 {list(CHECKPOINT_KINDS)} 之一")
        return normalized


class CheckpointRead(ORMModel):
    """检查点读模型。"""

    id: str
    run_id: str
    task_id: str | None = None
    loop_index: int = 0
    kind: str = "loop"
    snapshot: dict[str, Any] = Field(default_factory=dict)
    last_step_id: str | None = None
    last_event_seq: int = 0
    content_hash: str = ""
    created_at: datetime


class IdempotencyRecordRead(ORMModel):
    """幂等记录读模型。"""

    key: str
    scope: str = "api"
    step_id: str | None = None
    external_ref: str | None = None
    response_snapshot: dict[str, Any] = Field(default_factory=dict)
    request_fingerprint: str = ""
    created_at: datetime
    expires_at: datetime | None = None
    note: str | None = None
