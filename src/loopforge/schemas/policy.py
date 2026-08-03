"""策略域 DTO —— 策略包、规则、评估请求与判定结果。

策略是**默认拒绝**的：规则列表按 ``order`` 升序匹配，命中第一条即返回；
若一条都没命中，服务层必须返回 :attr:`PolicyDecision.DENY`。
本模块的 :class:`PolicyEvaluation` 因此把 ``decision`` 设成必填而非可选 —— 
「没有判定」这个状态在语义上不允许存在。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from loopforge.core.enums import PolicyDecision, Severity, SideEffectClass
from loopforge.schemas.common import LFModel, ORMModel

__all__ = [
    "POLICY_CATEGORIES",
    "PolicyRuleCreate",
    "PolicyRuleRead",
    "PolicyCreate",
    "PolicyRead",
    "PolicyDetail",
    "PolicyQuery",
    "PolicyPublishRequest",
    "PolicyEvaluationRequest",
    "PolicyEvaluation",
    "PolicyDryRunRequest",
    "PolicyDryRunResult",
]

#: 规则分类白名单。
POLICY_CATEGORIES: tuple[str, ...] = (
    "path",
    "shell",
    "git",
    "network",
    "cost",
    "tool",
    "default",
)


class PolicyRuleCreate(LFModel):
    """定义一条策略规则。"""

    rule_id: str = Field(min_length=1, max_length=64, description="规则编号，如 R-001")
    category: str = Field(min_length=1, max_length=32, description="规则分类")
    match: dict[str, object] = Field(default_factory=dict, description="匹配条件")
    effect: PolicyDecision = Field(description="命中后的效果")
    order: int = Field(default=0, ge=0, le=100_000, description="匹配顺序，越小越先")
    reason: str = Field(default="", max_length=2_000, description="判定理由模板")
    severity: Severity = Field(default=Severity.WARNING, description="严重级别")

    @field_validator("category")
    @classmethod
    def _check_category(cls, value: str) -> str:
        """分类必须在白名单内。"""
        normalized = value.strip().lower()
        if normalized not in POLICY_CATEGORIES:
            raise ValueError(f"category 必须是 {POLICY_CATEGORIES} 之一，收到 {value!r}")
        return normalized

    @model_validator(mode="after")
    def _check_match(self) -> PolicyRuleCreate:
        """非 default 分类必须给出匹配条件，否则等于无条件命中。"""
        if self.category != "default" and not self.match:
            raise ValueError(f"规则 {self.rule_id} 缺少 match 条件（只有 default 分类可省略）")
        return self


class PolicyRuleRead(ORMModel):
    """策略规则读模型。"""

    id: str = Field(description="规则行 ID")
    policy_id: str = Field(description="所属策略包")
    rule_id: str = Field(description="规则编号")
    category: str = Field(description="分类")
    match: dict[str, object] = Field(default_factory=dict, description="匹配条件")
    effect: PolicyDecision = Field(description="效果")
    order_index: int = Field(default=0, description="匹配顺序（对应 DB 列 order）")
    reason: str = Field(default="", description="理由模板")
    severity: Severity = Field(default=Severity.WARNING, description="严重级别")

    @property
    def is_catch_all(self) -> bool:
        """是否为兜底规则（无匹配条件）。"""
        return not self.match


class PolicyCreate(LFModel):
    """创建策略包（不直接激活，需再调 publish）。"""

    name: str = Field(min_length=1, max_length=100, description="策略名")
    yaml_source: str = Field(default="", max_length=1_000_000, description="原始 YAML")
    rules: list[PolicyRuleCreate] = Field(default_factory=list, description="规则列表")

    @model_validator(mode="after")
    def _check_rules(self) -> PolicyCreate:
        """规则编号不能重复，且至少要有一条。"""
        if not self.rules:
            raise ValueError("策略包至少需要一条规则")
        seen: set[str] = set()
        for rule in self.rules:
            if rule.rule_id in seen:
                raise ValueError(f"规则编号重复：{rule.rule_id}")
            seen.add(rule.rule_id)
        return self

    def sorted_rules(self) -> list[PolicyRuleCreate]:
        """按 ``order`` 升序返回规则副本。"""
        return sorted(self.rules, key=lambda rule: (rule.order, rule.rule_id))


class PolicyRead(ORMModel):
    """策略包读模型。"""

    id: str = Field(description="策略 ID")
    name: str = Field(description="策略名")
    version: int = Field(default=1, description="版本号")
    active: bool = Field(default=False, description="是否生效")
    checksum: str = Field(default="", description="内容校验和")
    activated_at: datetime | None = Field(default=None, description="激活时间")
    created_at: datetime = Field(description="创建时间")


class PolicyDetail(PolicyRead):
    """策略包详情（含规则与原始 YAML）。"""

    yaml_source: str = Field(default="", description="原始 YAML")
    compiled: dict[str, object] = Field(default_factory=dict, description="编译产物")
    rules: list[PolicyRuleRead] = Field(default_factory=list, description="规则列表")

    @property
    def rule_count(self) -> int:
        """规则条数。"""
        return len(self.rules)


class PolicyQuery(LFModel):
    """策略查询条件。"""

    name: str | None = Field(default=None, max_length=100, description="按名字过滤")
    active_only: bool = Field(default=False, description="只看生效版本")


class PolicyPublishRequest(LFModel):
    """发布（激活）一个策略版本。"""

    name: str = Field(min_length=1, max_length=100, description="策略名")
    version: int | None = Field(default=None, ge=1, description="目标版本，省略则取最新")
    reason: str = Field(default="", max_length=2_000, description="发布说明")


class PolicyEvaluationRequest(LFModel):
    """请求评估一个动作是否被允许。"""

    run_id: str = Field(min_length=1, max_length=26, description="所属 Run")
    task_id: str | None = Field(default=None, max_length=26, description="所属 Task")
    step_id: str | None = Field(default=None, max_length=26, description="所属 Step")
    action_kind: str = Field(min_length=1, max_length=64, description="动作类型，如 shell/git_push")
    side_effect_class: SideEffectClass = Field(
        default=SideEffectClass.PURE, description="副作用等级"
    )
    payload: dict[str, object] = Field(default_factory=dict, description="动作参数")
    policy_name: str | None = Field(default=None, max_length=100, description="指定策略包")

    @field_validator("action_kind")
    @classmethod
    def _normalize_kind(cls, value: str) -> str:
        """统一小写并去空白，避免 ``Shell`` / ``shell`` 命中不同规则。"""
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("action_kind 不能为空")
        return normalized


class PolicyEvaluation(LFModel):
    """策略判定结果。"""

    decision: PolicyDecision = Field(description="判定结论")
    rule_id: str | None = Field(default=None, description="命中的规则编号")
    reason: str = Field(default="", description="判定理由")
    severity: Severity = Field(default=Severity.INFO, description="严重级别")
    policy_id: str | None = Field(default=None, description="使用的策略包 ID")
    policy_version: int | None = Field(default=None, description="使用的策略版本")
    matched_at: datetime | None = Field(default=None, description="判定时间")
    requires_approval: bool = Field(default=False, description="是否需要人工审批")

    @model_validator(mode="after")
    def _sync_approval(self) -> PolicyEvaluation:
        """``REQUIRE_APPROVAL`` 与 ``requires_approval`` 必须一致，避免调用方看错字段。"""
        expected = self.decision is PolicyDecision.REQUIRE_APPROVAL
        if self.requires_approval != expected:
            object.__setattr__(self, "requires_approval", expected)
        return self

    @property
    def allowed(self) -> bool:
        """是否可以直接执行（审批态不算允许）。"""
        return self.decision is PolicyDecision.ALLOW

    @classmethod
    def deny_by_default(cls, reason: str = "未命中任何规则，按默认拒绝处理") -> PolicyEvaluation:
        """构造兜底拒绝结果。"""
        return cls(
            decision=PolicyDecision.DENY,
            rule_id="R-DEFAULT-DENY",
            reason=reason,
            severity=Severity.ERROR,
        )


class PolicyDryRunRequest(LFModel):
    """对一批动作做策略试跑，用于上线前回归。"""

    policy_name: str = Field(min_length=1, max_length=100, description="策略名")
    version: int | None = Field(default=None, ge=1, description="目标版本")
    samples: list[PolicyEvaluationRequest] = Field(
        default_factory=list, description="待试跑的动作样本"
    )

    @model_validator(mode="after")
    def _check_samples(self) -> PolicyDryRunRequest:
        """样本不能为空。"""
        if not self.samples:
            raise ValueError("samples 不能为空")
        if len(self.samples) > 1_000:
            raise ValueError("单次试跑样本不得超过 1000 条")
        return self


class PolicyDryRunResult(LFModel):
    """试跑结果。"""

    evaluations: list[PolicyEvaluation] = Field(default_factory=list, description="逐条判定")
    allow_count: int = Field(default=0, ge=0, description="放行数")
    deny_count: int = Field(default=0, ge=0, description="拒绝数")
    approval_count: int = Field(default=0, ge=0, description="需审批数")

    @classmethod
    def of(cls, evaluations: list[PolicyEvaluation]) -> PolicyDryRunResult:
        """从判定列表统计构造。"""
        allow = sum(1 for item in evaluations if item.decision is PolicyDecision.ALLOW)
        deny = sum(1 for item in evaluations if item.decision is PolicyDecision.DENY)
        approval = sum(
            1 for item in evaluations if item.decision is PolicyDecision.REQUIRE_APPROVAL
        )
        return cls(
            evaluations=evaluations,
            allow_count=allow,
            deny_count=deny,
            approval_count=approval,
        )
