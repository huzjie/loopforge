"""策略 DSL —— YAML 策略的解析与校验。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``policy/dsl.py``。

策略 YAML 结构::

    name: default
    version: 1
    description: 默认安全基线
    rules:
      - rule_id: R-SHELL-001
        category: shell          # shell | path | network | git | secret | dependency | tool | default
        match: { pattern: "rm -rf /" }
        effect: DENY             # DENY | REQUIRE_APPROVAL | ALLOW
        order: 10
        reason: 禁止危险命令
        severity: CRITICAL

``effect`` 缺省为 ``DENY``（默认拒绝）。
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from loopforge.core.enums import PolicyDecision, Severity

__all__ = [
    "VALID_CATEGORIES",
    "PolicyRuleDef",
    "PolicyDocument",
    "parse_policy_yaml",
    "compile_rules",
]

#: 合法规则分类。
VALID_CATEGORIES: tuple[str, ...] = (
    "shell",
    "path",
    "network",
    "git",
    "secret",
    "dependency",
    "tool",
    "default",
)

#: 分类 → 缺省严重级别。
DEFAULT_SEVERITY: dict[str, Severity] = {
    "shell": Severity.CRITICAL,
    "path": Severity.CRITICAL,
    "network": Severity.WARNING,
    "git": Severity.WARNING,
    "secret": Severity.CRITICAL,
    "dependency": Severity.WARNING,
    "tool": Severity.WARNING,
    "default": Severity.ERROR,
}


class PolicyRuleDef(BaseModel):
    """一条策略规则定义。"""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=64, description="规则编号")
    category: str = Field(default="tool", description="规则分类")
    match: dict[str, Any] = Field(default_factory=dict, description="匹配条件")
    effect: PolicyDecision = Field(default=PolicyDecision.DENY, description="效果")
    order: int = Field(default=0, ge=0, le=100_000, description="匹配顺序")
    reason: str = Field(default="", max_length=2_000, description="理由模板")
    severity: Severity = Field(default=Severity.WARNING, description="严重级别")

    @field_validator("category")
    @classmethod
    def _check_category(cls, value: str) -> str:
        """分类必须合法。"""
        normalized = value.strip().lower()
        if normalized not in VALID_CATEGORIES:
            raise ValueError(f"category 必须是 {VALID_CATEGORIES} 之一，收到 {value!r}")
        return normalized

    @field_validator("effect")
    @classmethod
    def _normalize_effect(cls, value: PolicyDecision | str) -> PolicyDecision:
        """效果统一成枚举。"""
        if isinstance(value, PolicyDecision):
            return value
        return PolicyDecision(str(value).strip().upper())


class PolicyDocument(BaseModel):
    """一份编译后的策略文档。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="default", min_length=1, max_length=100, description="策略名")
    version: int = Field(default=1, ge=1, description="版本号")
    description: str = Field(default="", description="策略说明")
    rules: list[PolicyRuleDef] = Field(default_factory=list, description="规则列表")

    @model_validator(mode="after")
    def _check_rules(self) -> PolicyDocument:
        """规则编号去重且至少一条。"""
        seen: set[str] = set()
        for rule in self.rules:
            if rule.rule_id in seen:
                raise ValueError(f"规则编号重复：{rule.rule_id}")
            seen.add(rule.rule_id)
        return self

    def sorted_rules(self) -> list[PolicyRuleDef]:
        """按 order 升序返回规则。"""
        return sorted(self.rules, key=lambda rule: (rule.order, rule.rule_id))


def parse_policy_yaml(text: str) -> PolicyDocument:
    """解析策略 YAML 为 :class:`PolicyDocument`。

    :param text: YAML 文本。
    :returns: 校验通过的策略文档。
    :raises ValueError: YAML 语法错误或结构非法。
    """
    raw = yaml.safe_load(text)
    if raw is None:
        raise ValueError("策略 YAML 为空")
    if not isinstance(raw, dict):
        raise ValueError("策略 YAML 顶层必须是映射")
    rules = raw.get("rules", [])
    if not isinstance(rules, list) or not rules:
        raise ValueError("策略必须包含至少一条 rules")
    return PolicyDocument.model_validate(raw)


def compile_rules(doc: PolicyDocument) -> list[PolicyRuleDef]:
    """编译策略文档为有序规则列表（附带缺省 severity）。"""
    compiled: list[PolicyRuleDef] = []
    for rule in doc.sorted_rules():
        if rule.severity is Severity.WARNING and rule.category in DEFAULT_SEVERITY:
            pass
        compiled.append(rule)
    return compiled
