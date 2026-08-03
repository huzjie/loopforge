"""策略引擎 —— ``PolicyEngine``（冻结契约 §11.7）。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2、§7.3.2（G1）、``ARCHITECTURE.md`` §7.5。

判定顺序（G1 **严格短路**）：``deny > require_approval > allow``。
遍历有序规则：

* 命中 ``DENY`` → **立即返回**，不再评估后续规则；
* 命中 ``REQUIRE_APPROVAL`` / ``ALLOW`` → 记录第一个命中，继续扫描
  （因为后面可能还有更优先的 ``DENY``）；
* 全部规则都没命中 → **默认拒绝**（``R-DEFAULT-DENY``）。

``PolicyAction`` / ``PolicyVerdict`` 字段与 ARCHITECTURE §7.5 逐字一致。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import PolicyDecision, Severity
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork
from loopforge.governance.policy.ast_analyzer import AstAnalyzer
from loopforge.governance.policy.dsl import PolicyDocument, PolicyRuleDef, compile_rules
from loopforge.governance.policy.rules.command import is_dangerous_shell
from loopforge.governance.policy.rules.dependency import DependencyRule
from loopforge.governance.policy.rules.git import GitRule
from loopforge.governance.policy.rules.network import NetworkRule
from loopforge.governance.policy.rules.path import PathRule
from loopforge.governance.policy.rules.secret import SecretRule

__all__ = ["PolicyEngine", "PolicyAction", "PolicyVerdict"]


class PolicyAction(BaseModel):
    """待评估动作（ARCHITECTURE §7.5 冻结）。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["command", "file_write", "file_read", "network", "git", "dependency", "secret"]
    tool_name: str = Field(default="", description="工具名")
    argv: list[str] = Field(default_factory=list, description="命令参数")
    path: str | None = Field(default=None, description="目标路径")
    url: str | None = Field(default=None, description="目标 URL")
    branch: str | None = Field(default=None, description="目标分支")
    content_sample: str | None = Field(default=None, description="内容样本")
    context: dict[str, Any] = Field(default_factory=dict, description="附加上下文")


class PolicyVerdict(BaseModel):
    """策略判定结果（ARCHITECTURE §7.5 冻结）。"""

    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecision = Field(description="判定结论")
    rule_id: str | None = Field(default=None, description="命中的规则编号")
    reason: str = Field(default="", description="判定理由")
    severity: Severity = Field(default=Severity.INFO, description="严重级别")
    matched_pattern: str | None = Field(default=None, description="命中的模式")


#: 分类 → PolicyAction.kind 的兼容映射。
_CATEGORY_KIND_MAP: dict[str, str] = {
    "shell": "command",
    "command": "command",
    "path": "file_write",
    "network": "network",
    "git": "git",
    "secret": "secret",
    "dependency": "dependency",
    "tool": "tool",
    "default": "default",
}


def _matches(rule: PolicyRuleDef, action: PolicyAction) -> tuple[bool, str]:
    """判断一条规则是否命中动作，返回 ``(命中, 证据)``。

    语义约定：对 ``ALLOW`` 规则，「命中」= 动作安全（白名单放行）；
    对 ``DENY`` / ``REQUIRE_APPROVAL`` 规则，「命中」= 动作危险。
    """
    category = rule.category
    match = rule.match
    effect = rule.effect

    # -- 工具名 / kind 过滤 ------------------------------------------------
    if "tool_name" in match:
        if action.tool_name and action.tool_name.lower() != str(match["tool_name"]).lower():
            return False, ""
    if "tool_names" in match:
        allowed = {str(name).lower() for name in match["tool_names"]}
        if action.tool_name and action.tool_name.lower() not in allowed:
            return False, ""
    if "kinds" in match:
        allowed_kinds = {str(kind).lower() for kind in match["kinds"]}
        if action.kind.lower() not in allowed_kinds:
            return False, ""

    # -- 分类判定 -----------------------------------------------------------
    if category == "default":
        return True, "兜底规则"
    if category == "tool":
        if "tool_name" in match or "tool_names" in match:
            return True, f"工具规则命中：{action.tool_name}"
        return False, ""
    if category in ("shell", "command"):
        if action.kind != "command":
            return False, ""
        dangerous, evidence = is_dangerous_shell(action.argv)
        if dangerous:
            return True, evidence
        # 规则自带 pattern 时再做一次正则。
        pattern = match.get("pattern")
        if pattern:
            text = " ".join(action.argv)
            import re

            if re.search(str(pattern), text, re.IGNORECASE):
                return True, f"命中命令正则：{pattern}"
        if effect is PolicyDecision.ALLOW:
            return True, "命令安全（白名单放行）"
        return False, ""
    if category == "path":
        if not action.path:
            return False, ""
        rule = PathRule(
            deny_globs=[str(item) for item in match.get("deny", [])],
            allow_globs=[str(item) for item in match.get("allow", [])],
            sandbox_root=str(match["sandbox_root"]) if match.get("sandbox_root") else None,
        )
        allowed, evidence = rule.evaluate(action.path)
        if effect is PolicyDecision.ALLOW:
            return (allowed, evidence if allowed else "")
        return (not allowed), evidence if not allowed else ""
    if category == "network":
        if not action.url:
            return False, ""
        rule = NetworkRule(
            allow_domains=[str(item) for item in match.get("allow_domains", [])],
            deny_domains=[str(item) for item in match.get("deny_domains", [])],
            allow_private=bool(match.get("allow_private", False)),
        )
        allowed, evidence = rule.evaluate(action.url)
        if effect is PolicyDecision.ALLOW:
            return (allowed, evidence if allowed else "")
        return (not allowed), evidence if not allowed else ""
    if category == "git":
        if action.kind != "git":
            return False, ""
        rule = GitRule(
            protected_branches=[str(item) for item in match.get("protected_branches", [])]
        )
        allowed, evidence = rule.evaluate(branch=action.branch, argv=action.argv)
        if effect is PolicyDecision.ALLOW:
            return (allowed, evidence if allowed else "")
        return (not allowed), evidence if not allowed else ""
    if category == "secret":
        if action.kind != "secret":
            return False, ""
        rule = SecretRule()
        safe, evidence = rule.evaluate(action.content_sample)
        if effect is PolicyDecision.ALLOW:
            return (safe, evidence if safe else "")
        return (not safe), evidence if not safe else ""
    if category == "dependency":
        if action.kind != "dependency":
            return False, ""
        rule = DependencyRule(
            deny_names=[str(item) for item in match.get("deny_names", [])],
            deny_version_patterns=[str(item) for item in match.get("deny_version_patterns", [])],
        )
        allowed, evidence = rule.evaluate(action.context)
        if effect is PolicyDecision.ALLOW:
            return (allowed, evidence if allowed else "")
        return (not allowed), evidence if not allowed else ""
    return False, ""


class PolicyEngine:
    """策略引擎。

    :param rules: 初始规则（:class:`PolicyDocument` 或规则定义列表）。
    :param session_factory: 传入时支持从 ``policies`` 表加载生效策略；
        ``None`` 则只使用内存规则。
    """

    name: ClassVar[str] = "policy"

    def __init__(
        self,
        *,
        rules: PolicyDocument | Sequence[PolicyRuleDef] | None = None,
        session_factory: Any | None = None,
    ) -> None:
        """初始化策略引擎。"""
        self._rules: list[PolicyRuleDef] = []
        self._policy_name: str = "default"
        self._session_factory: Any = session_factory if session_factory is not None else None
        if rules is not None:
            self.reload(rules)

    # -- 冻结出口：reload ---------------------------------------------------

    def reload(self, policy: PolicyDocument | Sequence[PolicyRuleDef]) -> None:
        """加载 / 替换内存规则。

        :param policy: 策略文档或规则定义列表。
        """
        if isinstance(policy, PolicyDocument):
            self._policy_name = policy.name
            self._rules = compile_rules(policy)
        else:
            self._rules = sorted(policy, key=lambda rule: (rule.order, rule.rule_id))

    # -- 冻结出口：evaluate -------------------------------------------------

    async def evaluate(self, action: PolicyAction) -> PolicyVerdict:
        """评估一个动作。

        :param action: 待评估动作。
        :returns: :class:`PolicyVerdict`。
        """
        rules = self._rules
        if self._session_factory is not None and not rules:
            rules = await self._load_active_from_db()
        first_match: PolicyRuleDef | None = None
        for rule in rules:
            matched, evidence = _matches(rule, action)
            if not matched:
                continue
            if rule.effect is PolicyDecision.DENY:
                # G1：先匹配到 deny 立即返回。
                return PolicyVerdict(
                    decision=PolicyDecision.DENY,
                    rule_id=rule.rule_id,
                    reason=evidence or rule.reason or "命中 deny 规则",
                    severity=rule.severity,
                    matched_pattern=evidence[:200] if evidence else None,
                )
            if first_match is None:
                first_match = rule
        if first_match is None:
            # 默认拒绝。
            return PolicyVerdict(
                decision=PolicyDecision.DENY,
                rule_id="R-DEFAULT-DENY",
                reason="未命中任何规则，按默认拒绝处理",
                severity=Severity.ERROR,
                matched_pattern=None,
            )
        return PolicyVerdict(
            decision=first_match.effect,
            rule_id=first_match.rule_id,
            reason=first_match.reason or f"命中规则 {first_match.rule_id}",
            severity=first_match.severity,
            matched_pattern=None,
        )

    # -- 冻结出口：evaluate_batch -------------------------------------------

    async def evaluate_batch(self, actions: Sequence[PolicyAction]) -> list[PolicyVerdict]:
        """批量评估。

        :param actions: 动作列表。
        :returns: 与输入等长的判定列表。
        """
        return [await self.evaluate(action) for action in actions]

    # -- 数据库加载 ---------------------------------------------------------

    async def _load_active_from_db(self) -> list[PolicyRuleDef]:
        """从 ``policies`` 表加载生效策略（同名最新 active 版本）。"""
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            policy = await uow.policies.get_active(self._policy_name)
            if policy is None:
                return []
            rows = await uow.policies.list_rules(policy.id)
        rules: list[PolicyRuleDef] = []
        for row in rows:
            rules.append(
                PolicyRuleDef(
                    rule_id=row.rule_id,
                    category=row.category,
                    match=dict(row.match or {}),
                    effect=row.effect,
                    order=row.order_index,
                    reason=row.reason,
                    severity=row.severity,
                )
            )
        return sorted(rules, key=lambda rule: (rule.order, rule.rule_id))
