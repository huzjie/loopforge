"""成本与预算域 DTO。

金额一律用 :class:`decimal.Decimal`，**禁止 float**。float 在累加十万次 LLM 调用
成本后会漂移到肉眼可见的程度，而这是要拿去跟账单对账的数字。

阈值语义（与 :class:`loopforge.core.enums.BudgetAction` 对齐）：

``warn_at`` < ``throttle_at`` < ``halt_at``，三者都是 0~1 的**比例**而非绝对值。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from loopforge.core.enums import BudgetAction, BudgetScope
from loopforge.schemas.common import LFModel, ORMModel

__all__ = [
    "BUDGET_WINDOWS",
    "BudgetCreate",
    "BudgetUpdate",
    "BudgetRead",
    "BudgetQuery",
    "BudgetCheckRequest",
    "BudgetCheckResult",
    "CostRecordCreate",
    "CostRecordRead",
    "CostQuery",
    "CostAggregate",
    "CostSummary",
]

#: 合法的预算窗口。
BUDGET_WINDOWS: tuple[str, ...] = ("total", "daily", "weekly", "monthly")

_ZERO = Decimal("0")
_ONE = Decimal("1")


class BudgetCreate(LFModel):
    """创建预算。"""

    scope: BudgetScope = Field(description="预算作用域")
    scope_ref: str = Field(min_length=1, max_length=64, description="作用域引用，如 run_id/日期")
    limit_usd: Decimal = Field(gt=_ZERO, description="额度上限（USD）")
    window: str = Field(default="total", max_length=16, description="预算窗口")
    warn_at: float = Field(default=0.5, gt=0.0, le=1.0, description="告警阈值比例")
    throttle_at: float = Field(default=0.8, gt=0.0, le=1.0, description="限流阈值比例")
    halt_at: float = Field(default=1.0, gt=0.0, le=1.0, description="停机阈值比例")
    window_start: datetime | None = Field(default=None, description="窗口起点")
    active: bool = Field(default=True, description="是否生效")

    @field_validator("window")
    @classmethod
    def _check_window(cls, value: str) -> str:
        """窗口必须在白名单内。"""
        normalized = value.strip().lower()
        if normalized not in BUDGET_WINDOWS:
            raise ValueError(f"window 必须是 {BUDGET_WINDOWS} 之一，收到 {value!r}")
        return normalized

    @model_validator(mode="after")
    def _check_thresholds(self) -> BudgetCreate:
        """三档阈值必须严格递增，否则限流永远轮不到。"""
        if not (self.warn_at < self.throttle_at <= self.halt_at):
            raise ValueError(
                "阈值必须满足 warn_at < throttle_at <= halt_at，"
                f"收到 {self.warn_at}/{self.throttle_at}/{self.halt_at}"
            )
        return self


class BudgetUpdate(LFModel):
    """更新预算（只允许调额度与阈值，不允许改作用域）。"""

    limit_usd: Decimal | None = Field(default=None, gt=_ZERO, description="新额度")
    warn_at: float | None = Field(default=None, gt=0.0, le=1.0, description="新告警阈值")
    throttle_at: float | None = Field(default=None, gt=0.0, le=1.0, description="新限流阈值")
    halt_at: float | None = Field(default=None, gt=0.0, le=1.0, description="新停机阈值")
    active: bool | None = Field(default=None, description="启停")

    def changed_fields(self) -> dict[str, object]:
        """返回本次实际提交的字段。"""
        return self.model_dump(exclude_none=True)


class BudgetRead(ORMModel):
    """预算读模型。"""

    id: str = Field(description="预算 ID")
    scope: BudgetScope = Field(description="作用域")
    scope_ref: str = Field(description="作用域引用")
    limit_usd: Decimal = Field(description="额度上限")
    window: str = Field(default="total", description="窗口")
    warn_at: float = Field(default=0.5, description="告警阈值")
    throttle_at: float = Field(default=0.8, description="限流阈值")
    halt_at: float = Field(default=1.0, description="停机阈值")
    consumed_usd: Decimal = Field(default=_ZERO, description="已消耗")
    window_start: datetime | None = Field(default=None, description="窗口起点")
    active: bool = Field(default=True, description="是否生效")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")

    @property
    def remaining_usd(self) -> Decimal:
        """剩余额度，永不为负。"""
        remaining = self.limit_usd - self.consumed_usd
        return remaining if remaining > _ZERO else _ZERO

    @property
    def usage_ratio(self) -> float:
        """已用比例；额度为 0 时返回 1.0（视为耗尽）。"""
        if self.limit_usd <= _ZERO:
            return 1.0
        return float(self.consumed_usd / self.limit_usd)

    @property
    def triggered_action(self) -> BudgetAction | None:
        """当前使用率触发的动作，未触发返回 ``None``。"""
        ratio = self.usage_ratio
        if ratio >= self.halt_at:
            return BudgetAction.HALT
        if ratio >= self.throttle_at:
            return BudgetAction.THROTTLE
        if ratio >= self.warn_at:
            return BudgetAction.WARN
        return None

    @property
    def is_exhausted(self) -> bool:
        """是否已经到达停机线。"""
        return self.triggered_action is BudgetAction.HALT


class BudgetQuery(LFModel):
    """预算查询条件。"""

    scope: BudgetScope | None = Field(default=None, description="按作用域过滤")
    scope_ref: str | None = Field(default=None, max_length=64, description="按引用过滤")
    window: str | None = Field(default=None, max_length=16, description="按窗口过滤")
    active_only: bool = Field(default=True, description="只看生效中的")


class BudgetCheckRequest(LFModel):
    """预检一次开销能否放行。"""

    run_id: str = Field(min_length=1, max_length=26, description="所属 Run")
    estimated_usd: Decimal = Field(ge=_ZERO, description="预估开销")
    scopes: tuple[BudgetScope, ...] = Field(
        default=(BudgetScope.RUN, BudgetScope.DAY, BudgetScope.ORG),
        description="需要一起检查的作用域",
    )

    @field_validator("scopes")
    @classmethod
    def _dedupe(cls, value: tuple[BudgetScope, ...]) -> tuple[BudgetScope, ...]:
        """去重且非空。"""
        seen: list[BudgetScope] = []
        for scope in value:
            if scope not in seen:
                seen.append(scope)
        if not seen:
            raise ValueError("scopes 不能为空")
        return tuple(seen)


class BudgetCheckResult(LFModel):
    """预检结果。"""

    allowed: bool = Field(description="是否放行")
    action: BudgetAction | None = Field(default=None, description="触发的动作")
    binding_scope: BudgetScope | None = Field(default=None, description="卡住的作用域")
    remaining_usd: Decimal = Field(default=_ZERO, description="最紧的剩余额度")
    reason: str = Field(default="", description="人类可读的原因")

    @classmethod
    def allow(cls, remaining_usd: Decimal = _ZERO) -> BudgetCheckResult:
        """构造一个放行结果。"""
        return cls(allowed=True, remaining_usd=remaining_usd, reason="预算充足")

    @classmethod
    def block(
        cls,
        *,
        action: BudgetAction,
        scope: BudgetScope,
        remaining_usd: Decimal = _ZERO,
        reason: str = "",
    ) -> BudgetCheckResult:
        """构造一个拦截结果。"""
        return cls(
            allowed=False,
            action=action,
            binding_scope=scope,
            remaining_usd=remaining_usd,
            reason=reason or f"{scope} 预算触发 {action}",
        )


class CostRecordCreate(LFModel):
    """记录一笔成本。"""

    run_id: str = Field(min_length=1, max_length=26, description="所属 Run")
    task_id: str | None = Field(default=None, max_length=26, description="所属 Task")
    pr_ref: str | None = Field(default=None, max_length=200, description="关联 PR")
    provider: str = Field(min_length=1, max_length=64, description="Provider 名")
    model: str = Field(min_length=1, max_length=200, description="模型名")
    prompt_tokens: int = Field(default=0, ge=0, description="输入 token")
    completion_tokens: int = Field(default=0, ge=0, description="输出 token")
    cost_usd: Decimal = Field(ge=_ZERO, description="金额（USD）")
    llm_call_id: str | None = Field(default=None, max_length=26, description="关联 LLM 调用")
    budget_id: str | None = Field(default=None, max_length=26, description="计入的预算")


class CostRecordRead(ORMModel):
    """成本记录读模型。"""

    id: str = Field(description="记录 ID")
    run_id: str = Field(description="所属 Run")
    task_id: str | None = Field(default=None, description="所属 Task")
    pr_ref: str | None = Field(default=None, description="关联 PR")
    provider: str = Field(description="Provider")
    model: str = Field(description="模型")
    prompt_tokens: int = Field(default=0, description="输入 token")
    completion_tokens: int = Field(default=0, description="输出 token")
    cost_usd: Decimal = Field(default=_ZERO, description="金额")
    llm_call_id: str | None = Field(default=None, description="关联 LLM 调用")
    budget_id: str | None = Field(default=None, description="计入的预算")
    occurred_at: datetime = Field(description="发生时间")

    @property
    def total_tokens(self) -> int:
        """输入 + 输出 token。"""
        return self.prompt_tokens + self.completion_tokens


class CostQuery(LFModel):
    """成本查询条件。"""

    run_id: str | None = Field(default=None, max_length=26, description="按 Run 过滤")
    task_id: str | None = Field(default=None, max_length=26, description="按 Task 过滤")
    provider: str | None = Field(default=None, max_length=64, description="按 Provider 过滤")
    model: str | None = Field(default=None, max_length=200, description="按模型过滤")
    occurred_after: datetime | None = Field(default=None, description="起始时间")
    occurred_before: datetime | None = Field(default=None, description="结束时间")

    @model_validator(mode="after")
    def _check_window(self) -> CostQuery:
        """时间窗必须非逆序。"""
        if (
            self.occurred_after is not None
            and self.occurred_before is not None
            and self.occurred_after > self.occurred_before
        ):
            raise ValueError("occurred_after 不能晚于 occurred_before")
        return self


class CostAggregate(LFModel):
    """一个维度上的成本聚合。"""

    key: str = Field(description="维度取值，如 provider 名或 model 名")
    cost_usd: Decimal = Field(default=_ZERO, description="小计金额")
    prompt_tokens: int = Field(default=0, ge=0, description="小计输入 token")
    completion_tokens: int = Field(default=0, ge=0, description="小计输出 token")
    call_count: int = Field(default=0, ge=0, description="调用次数")

    @property
    def avg_cost_usd(self) -> Decimal:
        """平均单次成本。"""
        if self.call_count <= 0:
            return _ZERO
        return self.cost_usd / Decimal(self.call_count)


class CostSummary(LFModel):
    """成本汇总视图。"""

    total_usd: Decimal = Field(default=_ZERO, description="总金额")
    total_calls: int = Field(default=0, ge=0, description="总调用次数")
    total_tokens: int = Field(default=0, ge=0, description="总 token")
    by_provider: list[CostAggregate] = Field(default_factory=list, description="按 Provider 聚合")
    by_model: list[CostAggregate] = Field(default_factory=list, description="按模型聚合")
    window_start: datetime | None = Field(default=None, description="统计窗口起")
    window_end: datetime | None = Field(default=None, description="统计窗口止")

    @property
    def cost_per_1k_tokens(self) -> Decimal:
        """每千 token 平均成本。"""
        if self.total_tokens <= 0:
            return _ZERO
        return self.total_usd / (Decimal(self.total_tokens) / Decimal(1000))
