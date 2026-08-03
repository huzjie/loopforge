"""命令策略引擎（守卫） —— 工具层与执行层的统一裁决入口。

关系澄清
--------
:mod:`loopforge.executors.command_policy` 提供底层正则规则引擎
(:class:`~loopforge.executors.command_policy.CommandPolicy`) 与默认规则集。
本模块在其之上补三件事：

1. **精确匹配规则**：除了正则，还要支持「程序名精确黑名单 / 精确白名单」，
   例如 ``argv[0] == "sudo"`` 直接拒绝，不依赖正则写法。
2. **PolicyAction 适配层**：WP5 的 ``PolicyEngine`` 尚未交付，但工具层
   （WP3）与编排层（WP4）都需要一个统一动作类型。这里按
   ``ARCHITECTURE.md`` §7.5 冻结字段定义 :class:`PolicyAction` /
   :class:`PolicyVerdict`，并提供 :meth:`CommandPolicyGuard.evaluate_action`，
   使下游可以先按冻结契约编码，WP5 交付后无缝替换。
3. **命中写 AuditLog**：任何非 ``ALLOW`` 裁决都写 ``audit_logs`` 表
   （复用 :class:`~loopforge.executors.command_policy.DbAuditSink`）。

安全定位：**这是执行层的最后一道闸**。工具层可以被插件绕过，但任何命令
最终都要经 :meth:`CommandPolicyGuard.enforce` 放行才能到达
:meth:`LocalSubprocessExecutor.run`。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import PolicyDecision, Severity
from loopforge.core.errors import ApprovalRequiredError, PolicyDeniedError
from loopforge.core.logging import get_logger
from loopforge.executors.command_policy import (
    AuditSink,
    CommandPolicy,
    CommandRule,
    CommandVerdict,
    NullAuditSink,
)

__all__ = [
    "PolicyAction",
    "PolicyVerdict",
    "exact_deny_rule",
    "exact_allow_rule",
    "CommandPolicyGuard",
    "DEFAULT_EXACT_DENY_PROGRAMS",
    "DEFAULT_EXACT_APPROVAL_PROGRAMS",
]

_log = get_logger(__name__)


class PolicyAction(BaseModel):
    """一次待裁决动作（**字段冻结**，ARCHITECTURE §7.5）。

    工具层构造 :class:`PolicyAction` 提交给策略引擎；执行层构造的
    :class:`~loopforge.executors.command_policy.CommandVerdict` 与之等价。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["command", "file_write", "file_read", "network", "git", "dependency", "secret"]
    tool_name: str = Field(min_length=1)
    argv: list[str] = Field(default_factory=list)
    path: str | None = None
    url: str | None = None
    branch: str | None = None
    content_sample: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class PolicyVerdict(BaseModel):
    """策略裁决结果（**字段冻结**，ARCHITECTURE §7.5）。"""

    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecision = PolicyDecision.ALLOW
    rule_id: str | None = None
    reason: str = ""
    severity: Severity = Severity.INFO
    matched_pattern: str | None = None

    @property
    def allowed(self) -> bool:
        """是否直接放行。"""
        return self.decision is PolicyDecision.ALLOW


def exact_deny_rule(
    rule_id: str, program: str, reason: str, *, severity: Severity = Severity.ERROR
) -> CommandRule:
    """构造「argv[0] 精确等于某程序名 → 拒绝」的规则。

    Args:
        rule_id: 规则 ID。
        program: 程序名（不含路径与 ``.exe`` 后缀）。
        reason: 拒绝原因。
        severity: 严重级。

    Returns:
        一条 ``match_on="argv0"`` 的精确匹配规则。
    """
    escaped = re.escape(program)
    return CommandRule(
        rule_id=rule_id,
        pattern=rf"^{escaped}$",
        decision=PolicyDecision.DENY,
        reason=reason,
        severity=severity,
        match_on="argv0",
    )


def exact_allow_rule(rule_id: str, program: str, reason: str = "") -> CommandRule:
    """构造「argv[0] 精确等于某程序名 → 拒绝」的反向规则（占位用，勿直接使用）。

    说明：白名单精确匹配由 :class:`CommandPolicy` 的 ``argv0_allowlist`` 承担，
    这里仅为对称性保留构造器，实际场景请使用 ``allowlist_only=True``。
    """
    escaped = re.escape(program)
    return CommandRule(
        rule_id=rule_id,
        pattern=rf"^{escaped}$",
        decision=PolicyDecision.ALLOW,
        reason=reason,
        severity=Severity.INFO,
        match_on="argv0",
    )


#: 精确拒绝的程序名 —— 一旦出现没有任何正当解释。
DEFAULT_EXACT_DENY_PROGRAMS: dict[str, str] = {
    "sudo": "提权执行",
    "doas": "提权执行",
    "su": "切换用户",
    "mount": "挂载文件系统",
    "umount": "卸载文件系统",
    "mkfs": "格式化文件系统",
    "diskpart": "磁盘分区",
    "shutdown": "关机",
    "reboot": "重启",
    "halt": "关机",
    "poweroff": "关机",
    "crontab": "植入定时任务",
    "vssadmin": "卷影副本管理（勒索行为）",
    "bcdedit": "修改引导配置",
    "regedit": "注册表编辑器",
}

#: 精确要求审批的程序名 —— 有正当用途但后果不可自动承担。
DEFAULT_EXACT_APPROVAL_PROGRAMS: dict[str, str] = {
    "terraform": "改动云基础设施",
    "kubectl": "改动 Kubernetes 集群",
}


class CommandPolicyGuard:
    """命令策略守卫：正则规则 + 精确规则 + PolicyAction 适配 + 审计。

    Args:
        policy: 底层正则规则引擎（默认 :class:`CommandPolicy`）。
        exact_deny: 精确拒绝的程序名 → 原因映射。
        exact_approval: 精确审批的程序名 → 原因映射。
        audit_sink: 审计出口；默认 :class:`NullAuditSink`（生产请注入
            :class:`~loopforge.executors.command_policy.DbAuditSink`）。

    Example:
        >>> guard = CommandPolicyGuard()
        >>> guard.evaluate(["rm", "-rf", "/"]).decision.value
        'DENY'
        >>> guard.evaluate(["git", "status"]).decision.value
        'ALLOW'
    """

    def __init__(
        self,
        *,
        policy: CommandPolicy | None = None,
        exact_deny: dict[str, str] | None = None,
        exact_approval: dict[str, str] | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._policy: CommandPolicy = policy or CommandPolicy()
        self._audit: AuditSink = audit_sink or NullAuditSink()
        deny = {**DEFAULT_EXACT_DENY_PROGRAMS, **(exact_deny or {})}
        approval = {**DEFAULT_EXACT_APPROVAL_PROGRAMS, **(exact_approval or {})}
        self._exact_deny: tuple[CommandRule, ...] = tuple(
            exact_deny_rule(f"deny.exact.{name}", name, reason)
            for name, reason in sorted(deny.items())
        )
        self._exact_approval: tuple[CommandRule, ...] = tuple(
            exact_deny_rule(
                f"approval.exact.{name}",
                name,
                reason,
                severity=Severity.WARNING,
            )
            for name, reason in sorted(approval.items())
        )

    # ------------------------------------------------------------------ 裁决

    def evaluate(self, argv: Sequence[str]) -> CommandVerdict:
        """对 argv 数组做裁决（纯函数，不抛异常不写审计）。

        Args:
            argv: 命令数组。

        Returns:
            :class:`CommandVerdict`。
        """
        items = [str(item) for item in argv]
        if not items:
            return CommandVerdict(
                decision=PolicyDecision.DENY,
                rule_id="deny.empty_argv",
                reason="argv 为空",
                severity=Severity.ERROR,
                argv=items,
            )
        program = _program_name(items[0])

        for rule in self._exact_deny:
            if rule.matches(items, " ".join(items)) is not None:
                return CommandVerdict(
                    decision=PolicyDecision.DENY,
                    rule_id=rule.rule_id,
                    reason=rule.reason,
                    matched=program,
                    severity=rule.severity,
                    argv=items,
                )
        for rule in self._exact_approval:
            if rule.matches(items, " ".join(items)) is not None:
                return CommandVerdict(
                    decision=PolicyDecision.REQUIRE_APPROVAL,
                    rule_id=rule.rule_id,
                    reason=rule.reason,
                    matched=program,
                    severity=rule.severity,
                    argv=items,
                )
        return self._policy.evaluate(items)

    async def enforce(
        self, argv: Sequence[str], *, context: dict[str, Any] | None = None
    ) -> CommandVerdict:
        """裁决 + 审计 + 抛异常（Executor 的最后一层闸）。

        Args:
            argv: 命令数组。
            context: 审计上下文（run_id / task_id / step_id / trace_id / cwd）。

        Returns:
            放行的裁决。

        Raises:
            PolicyDeniedError: 命中拒绝规则。
            ApprovalRequiredError: 命中审批规则。
        """
        verdict = self.evaluate(argv)
        if verdict.allowed:
            return verdict
        ctx = dict(context or {})
        await self._audit.record(verdict, context=ctx)
        if verdict.decision is PolicyDecision.DENY:
            raise PolicyDeniedError(
                f"命令被策略拒绝（{verdict.rule_id}）：{verdict.reason}",
                details=verdict.to_details() | ctx,
            )
        raise ApprovalRequiredError(
            f"命令需要人工审批（{verdict.rule_id}）：{verdict.reason}",
            details=verdict.to_details() | ctx,
        )

    # ------------------------------------------------------------------ PolicyAction 适配

    async def evaluate_action(self, action: PolicyAction) -> PolicyVerdict:
        """裁决一个 :class:`PolicyAction`（工具层调用）。

        命令类动作直接走 argv 裁决；文件 / 网络 / git 类动作按 kind 分派到
        底层正则规则（当前以 argv 字段承载，语义一致）。

        Args:
            action: 待裁决动作。

        Returns:
            :class:`PolicyVerdict`。
        """
        if action.kind == "command":
            verdict = self.evaluate(action.argv)
            return PolicyVerdict(
                decision=verdict.decision,
                rule_id=verdict.rule_id,
                reason=verdict.reason,
                severity=verdict.severity,
                matched_pattern=verdict.matched or None,
            )
        # 非命令类动作：用工具名 + 目标构成一个「虚拟 argv」做正则扫描。
        probe = [action.tool_name]
        if action.path:
            probe.append(action.path)
        if action.url:
            probe.append(action.url)
        if action.branch:
            probe.append(action.branch)
        verdict = self.evaluate(probe)
        return PolicyVerdict(
            decision=verdict.decision,
            rule_id=verdict.rule_id,
            reason=verdict.reason,
            severity=verdict.severity,
            matched_pattern=verdict.matched or None,
        )

    async def evaluate_actions(self, actions: Sequence[PolicyAction]) -> list[PolicyVerdict]:
        """批量裁决。"""
        return [await self.evaluate_action(action) for action in actions]

    # ------------------------------------------------------------------ 规则管理

    def add_rule(self, rule: CommandRule) -> None:
        """追加一条自定义正则规则（优先级高于内置）。"""
        self._policy.add_rule(rule)

    def rules(self) -> list[CommandRule]:
        """当前全部规则（精确拒绝 → 精确审批 → 底层规则）。"""
        return [*self._exact_deny, *self._exact_approval, *self._policy.rules()]

    def reload(self, deny_rules: Sequence[CommandRule] | None = None) -> None:
        """重建底层规则集（PolicyEngine.reload 兼容接口）。

        Args:
            deny_rules: 新拒绝规则；``None`` 表示重置为默认。
        """
        self._policy = CommandPolicy(
            deny_rules=deny_rules if deny_rules is not None else None
        )

    @property
    def policy(self) -> CommandPolicy:
        """底层规则引擎（需要细粒度控制时直接取用）。"""
        return self._policy

    @property
    def audit_sink(self) -> AuditSink:
        """当前审计出口。"""
        return self._audit


def _program_name(argv0: str) -> str:
    """从 ``argv[0]`` 提取程序名（去路径、去 ``.exe``）。"""
    head = argv0.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    return head[:-4] if head.endswith(".exe") else head
