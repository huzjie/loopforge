"""命令策略引擎 —— 执行层的第二道闸（双保险的最后一层）。

为什么工具层已经过一遍策略、这里还要再过一遍
--------------------------------------------
工具层的 ``PolicyEngine`` 可以被第三方插件绕过（插件自己拿 executor 就能执行），
而 **Executor 是唯一的物理出口**。安全设计里这叫「choke point」：
把不可绕过的检查放在唯一出口上，而不是放在若干可选入口上。

裁决语义
--------
:class:`~loopforge.core.enums.PolicyDecision` 三值：

* ``ALLOW`` —— 放行。
* ``DENY`` —— 拒绝，抛 :class:`~loopforge.core.errors.PolicyDeniedError`。
* ``REQUIRE_APPROVAL`` —— 需要人工审批，抛
  :class:`~loopforge.core.errors.ApprovalRequiredError`，由 WP4 挂起 Step。

**命中即写审计**：任何非 ``ALLOW`` 的裁决都会经 :class:`AuditSink` 落盘，
这是事后复盘「Agent 到底想干什么」的唯一凭据。
"""

from __future__ import annotations

import abc
import re
import shlex
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from loopforge.core.clock import Clock, get_clock
from loopforge.core.enums import PolicyDecision, Severity
from loopforge.core.errors import ApprovalRequiredError, PolicyDeniedError
from loopforge.core.logging import get_logger

__all__ = [
    "SHELL_BINARIES",
    "SHELL_METACHARACTERS",
    "DEFAULT_DENY_RULES",
    "DEFAULT_APPROVAL_RULES",
    "DEFAULT_ARGV0_ALLOWLIST",
    "CommandRule",
    "CommandVerdict",
    "AuditSink",
    "NullAuditSink",
    "MemoryAuditSink",
    "DbAuditSink",
    "CommandPolicy",
]

_log = get_logger(__name__)

#: 会把参数当脚本解释的程序 —— 对它们的参数要做全文扫描。
SHELL_BINARIES: frozenset[str] = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "dash",
        "ksh",
        "csh",
        "fish",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "wsl",
        "wsl.exe",
        "busybox",
        "env",
        "xargs",
        "eval",
    }
)

#: shell 元字符 —— 出现在 shell 类程序的参数里即触发全文扫描。
SHELL_METACHARACTERS: tuple[str, ...] = (";", "&&", "||", "|", "`", "$(", "\n", ">", "<", "&")


class CommandRule(BaseModel):
    """单条命令策略规则。"""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=100)
    pattern: str = Field(min_length=1, description="Python 正则，匹配时忽略大小写")
    decision: PolicyDecision = PolicyDecision.DENY
    reason: str = Field(default="", max_length=500)
    severity: Severity = Severity.ERROR
    match_on: Literal["argv0", "joined", "any_arg"] = "joined"
    enabled: bool = True

    @field_validator("pattern")
    @classmethod
    def _compilable(cls, value: str) -> str:
        """确保正则可编译，避免坏规则在运行期才炸。

        Raises:
            ValueError: 正则非法。
        """
        try:
            re.compile(value, re.IGNORECASE)
        except re.error as exc:  # pragma: no cover - 仅防御坏配置
            raise ValueError(f"pattern 不是合法正则：{exc}") from exc
        return value

    def matches(self, argv: Sequence[str], joined: str) -> str | None:
        """判断该规则是否命中。

        Args:
            argv: 命令数组。
            joined: 已拼接的完整命令字符串（仅用于匹配，不用于执行）。

        Returns:
            命中的文本片段；未命中返回 ``None``。
        """
        if not self.enabled:
            return None
        regex = re.compile(self.pattern, re.IGNORECASE)
        if self.match_on == "argv0":
            head = argv[0] if argv else ""
            found = regex.search(head)
            return found.group(0) if found else None
        if self.match_on == "any_arg":
            for item in argv:
                found = regex.search(item)
                if found:
                    return found.group(0)
            return None
        found = regex.search(joined)
        return found.group(0) if found else None


class CommandVerdict(BaseModel):
    """一次命令裁决的完整结果。"""

    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecision = PolicyDecision.ALLOW
    rule_id: str | None = None
    reason: str = ""
    matched: str = ""
    severity: Severity = Severity.INFO
    argv: list[str] = Field(default_factory=list)

    @property
    def allowed(self) -> bool:
        """是否直接放行。"""
        return self.decision is PolicyDecision.ALLOW

    def to_details(self) -> dict[str, Any]:
        """转成异常 / 审计用的 details 字典。"""
        return {
            "decision": self.decision.value,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "matched": self.matched,
            "argv": list(self.argv),
        }


def _rule(
    rule_id: str,
    pattern: str,
    reason: str,
    *,
    decision: PolicyDecision = PolicyDecision.DENY,
    severity: Severity = Severity.ERROR,
    match_on: Literal["argv0", "joined", "any_arg"] = "joined",
) -> CommandRule:
    """规则构造糖。"""
    return CommandRule(
        rule_id=rule_id,
        pattern=pattern,
        decision=decision,
        reason=reason,
        severity=severity,
        match_on=match_on,
    )


#: 硬拒绝规则 —— 这些命令没有任何「其实我是想…」的合理解释。
DEFAULT_DENY_RULES: tuple[CommandRule, ...] = (
    # ---- 破坏性删除 ----
    _rule(
        "deny.rm_rf_root",
        r"\brm\b(?=[^|;]*\s-{1,2}[a-zA-Z]*r[a-zA-Z]*)(?=[^|;]*\s-{1,2}[a-zA-Z]*f[a-zA-Z]*)(?=[^|;]*\s(?:/|/\*|~|~/|\$HOME|C:\\))",
        "递归强删根目录 / 家目录 / 盘符（含 -r -f 任意顺序与长选项）",
        severity=Severity.CRITICAL,
    ),
    _rule(
        "deny.rm_rf_wildcard",
        r"\brm\s+(-[a-z]*\s+)*-[a-z]*[rR][a-z]*[fF][a-z]*\s+\*(\s|$)",
        "递归强删当前目录全部内容",
        severity=Severity.CRITICAL,
    ),
    _rule("deny.rm_no_preserve", r"--no-preserve-root", "显式解除根目录保护"),
    _rule(
        "deny.windows_del_root",
        r"\bdel\b.*\s/[sS]\b.*\s[a-zA-Z]:\\?(\s|$)|\brd\b\s+/[sS]\s+/[qQ]\s+[a-zA-Z]:\\",
        "Windows 递归删除盘符根",
        severity=Severity.CRITICAL,
    ),
    _rule(
        "deny.powershell_remove_root",
        r"remove-item\b[^|;]*-recurse[^|;]*(-force)?[^|;]*\s[a-zA-Z]:\\?(\s|$|\")",
        "PowerShell 递归强删盘符根",
        severity=Severity.CRITICAL,
    ),
    # ---- 磁盘与文件系统 ----
    _rule("deny.mkfs", r"\bmkfs(\.[a-z0-9]+)?\b", "格式化文件系统", severity=Severity.CRITICAL),
    _rule("deny.format", r"\bformat\s+[a-zA-Z]:", "格式化盘符", severity=Severity.CRITICAL),
    _rule("deny.diskpart", r"\bdiskpart\b", "磁盘分区工具", severity=Severity.CRITICAL),
    _rule(
        "deny.dd_device",
        r"\bdd\b[^|;]*\bof=/dev/(sd|nvme|hd|disk|mmcblk)",
        "dd 直写块设备",
        severity=Severity.CRITICAL,
    ),
    _rule("deny.redirect_device", r">\s*/dev/(sd|nvme|hd|mem|kmem)", "重定向写入块设备"),
    _rule("deny.vssadmin", r"\bvssadmin\b.*\bdelete\b", "删除卷影副本（勒索软件行为）"),
    _rule("deny.bcdedit", r"\bbcdedit\b", "修改引导配置"),
    # ---- 资源耗尽 ----
    _rule("deny.fork_bomb", r":\s*\(\s*\)\s*\{.*\|\s*:\s*&.*\}\s*;?\s*:", "fork bomb"),
    _rule("deny.fork_bomb_alt", r"\bwhile\s+true\s*;\s*do\s+.*&\s*done", "无限后台派生循环"),
    _rule("deny.yes_pipe", r"\byes\b\s*\|\s*(?!head)", "yes 无限输出管道"),
    # ---- 远程代码执行 ----
    _rule(
        "deny.curl_pipe_shell",
        r"\b(curl|wget|iwr|invoke-webrequest)\b[^|]*\|\s*(sudo\s+)?(ba|z|k|da)?sh\b",
        "下载即执行（curl | sh）",
        severity=Severity.CRITICAL,
    ),
    _rule(
        "deny.powershell_download_exec",
        r"(downloadstring|downloadfile)\s*\(.*\)\s*\||iex\s*\(",
        "PowerShell 下载即执行",
        severity=Severity.CRITICAL,
    ),
    _rule("deny.powershell_encoded", r"\s-(e|en|enc|encodedcommand)\s+[A-Za-z0-9+/=]{20,}", "PowerShell base64 混淆命令"),
    _rule("deny.certutil_download", r"\bcertutil\b.*-urlcache", "certutil 充当下载器"),
    _rule("deny.nc_backdoor", r"\bn(c|cat)\b.*\s-e\s", "netcat 反弹 shell"),
    _rule("deny.bash_reverse_shell", r"/dev/(tcp|udp)/", "bash 反弹 shell"),
    # ---- 提权与账户 ----
    _rule("deny.sudo", r"^sudo$|^doas$", "禁止提权执行", match_on="argv0"),
    _rule("deny.su_root", r"\bsu\s+(-\s+)?root\b", "切换到 root"),
    _rule("deny.net_user_add", r"\bnet\s+user\b.*\s/add\b", "创建 Windows 账户"),
    _rule("deny.net_localgroup", r"\bnet\s+localgroup\b.*\s/add\b", "提升本地组权限"),
    _rule("deny.chmod_777_root", r"\bchmod\b\s+(-R\s+)?777\s+/(\s|$)", "放开根目录权限"),
    _rule("deny.chown_root", r"\bchown\b\s+(-R\s+)?[^\s]+\s+/(\s|$)", "变更根目录属主"),
    # ---- 敏感读取与外泄 ----
    _rule("deny.read_shadow", r"/etc/(shadow|gshadow|sudoers)\b", "读取影子口令文件"),
    _rule("deny.read_ssh_key", r"(\.ssh/(id_[a-z0-9]+|authorized_keys)|\.aws/credentials)", "读取私钥 / 云凭据"),
    _rule("deny.sam_hive", r"\\config\\SAM\b|\bntds\.dit\b", "读取 Windows 凭据库"),
    _rule("deny.history_wipe", r"\bhistory\s+-c\b|\.bash_history\b.*>", "清除命令历史（反取证）"),
    # ---- 注册表与系统服务 ----
    _rule("deny.reg_write", r"\breg\b\s+(add|delete|import|copy|restore)\b", "写注册表"),
    _rule("deny.registry_cmdlet", r"\b(set|remove|new)-itemproperty\b.*\bhk(lm|cu|cr|u|cc):", "PowerShell 写注册表"),
    _rule("deny.shutdown", r"\b(shutdown|reboot|halt|poweroff)\b", "关机 / 重启宿主"),
    _rule("deny.systemctl_mutate", r"\bsystemctl\s+(start|stop|restart|disable|mask)\b", "改动系统服务"),
    _rule("deny.iptables", r"\b(iptables|nft|ufw|netsh\s+advfirewall)\b", "改动防火墙规则"),
    _rule("deny.crontab", r"\bcrontab\b|\bschtasks\b\s*/create", "植入定时任务（持久化）"),
    _rule("deny.takeown", r"\b(takeown|icacls)\b.*\s[a-zA-Z]:\\(windows|program)", "接管系统目录属主"),
    # ---- 容器逃逸 ----
    _rule("deny.docker_privileged", r"\bdocker\b[^|;]*--privileged", "特权容器（逃逸跳板）"),
    _rule("deny.docker_mount_root", r"\bdocker\b[^|;]*-v\s*/:\s*/", "把宿主根挂进容器"),
    _rule("deny.mount", r"^mount$|^umount$", "挂载文件系统", match_on="argv0"),
    # ---- 进程 ----
    _rule("deny.kill_all", r"\bkillall\b\s+-9\s+\w|\bkill\s+-9\s+-1\b", "无差别杀进程"),
)

#: 需要人工审批的规则 —— 有正当用途，但后果不可自动承担。
DEFAULT_APPROVAL_RULES: tuple[CommandRule, ...] = (
    _rule(
        "approval.git_force_push",
        r"\bgit\b[^|;]*\bpush\b[^|;]*(--force(?!-with-lease)|\s-f\b)",
        "强制推送会覆盖他人提交",
        decision=PolicyDecision.REQUIRE_APPROVAL,
        severity=Severity.WARNING,
    ),
    _rule(
        "approval.git_reset_hard",
        r"\bgit\b[^|;]*\breset\b[^|;]*--hard",
        "硬重置会丢弃未提交改动",
        decision=PolicyDecision.REQUIRE_APPROVAL,
        severity=Severity.WARNING,
    ),
    _rule(
        "approval.git_clean",
        r"\bgit\b[^|;]*\bclean\b[^|;]*-[a-z]*f",
        "git clean -f 会删除未跟踪文件",
        decision=PolicyDecision.REQUIRE_APPROVAL,
        severity=Severity.WARNING,
    ),
    _rule(
        "approval.package_publish",
        r"\b(npm|yarn|pnpm)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b",
        "向公共仓库发布制品",
        decision=PolicyDecision.REQUIRE_APPROVAL,
        severity=Severity.WARNING,
    ),
    _rule(
        "approval.global_install",
        r"\b(npm|pnpm|yarn)\s+(install|add)\b[^|;]*\s(-g|--global)\b|\bpip\s+install\b[^|;]*--user\b",
        "全局安装会污染宿主环境",
        decision=PolicyDecision.REQUIRE_APPROVAL,
        severity=Severity.NOTICE,
    ),
    _rule(
        "approval.kubectl_mutate",
        r"\bkubectl\s+(apply|delete|scale|drain|cordon)\b",
        "改动 Kubernetes 集群状态",
        decision=PolicyDecision.REQUIRE_APPROVAL,
        severity=Severity.WARNING,
    ),
    _rule(
        "approval.terraform_apply",
        r"\bterraform\s+(apply|destroy)\b",
        "改动云基础设施",
        decision=PolicyDecision.REQUIRE_APPROVAL,
        severity=Severity.WARNING,
    ),
)

#: 允许模式下的可执行程序白名单（``allowlist_only=True`` 时生效）。
DEFAULT_ARGV0_ALLOWLIST: frozenset[str] = frozenset(
    {
        "git",
        "python",
        "python3",
        "py",
        "pytest",
        "ruff",
        "mypy",
        "black",
        "pip",
        "uv",
        "node",
        "npm",
        "npx",
        "pnpm",
        "yarn",
        "vitest",
        "jest",
        "tsc",
        "go",
        "cargo",
        "rustc",
        "make",
        "cmake",
        "java",
        "mvn",
        "gradle",
        "dotnet",
        "ls",
        "dir",
        "cat",
        "type",
        "echo",
        "pwd",
        "cd",
        "cp",
        "mv",
        "mkdir",
        "touch",
        "find",
        "grep",
        "rg",
        "sed",
        "awk",
        "head",
        "tail",
        "wc",
        "sort",
        "diff",
        "patch",
        "tar",
        "unzip",
        "where",
        "which",
    }
)


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------


class AuditSink(abc.ABC):
    """策略裁决的审计出口。"""

    @abc.abstractmethod
    async def record(self, verdict: CommandVerdict, *, context: dict[str, Any]) -> None:
        """记录一次非 ALLOW 裁决。

        Args:
            verdict: 裁决结果。
            context: 附加上下文（run_id / task_id / step_id / trace_id / cwd）。
        """
        raise NotImplementedError


class NullAuditSink(AuditSink):
    """不落盘，只打结构化日志（单测与无 DB 场景的默认实现）。"""

    async def record(self, verdict: CommandVerdict, *, context: dict[str, Any]) -> None:
        """打一条 WARNING 日志。"""
        _log.warning(
            "command_policy_hit",
            decision=verdict.decision.value,
            rule_id=verdict.rule_id,
            reason=verdict.reason,
            **{k: v for k, v in context.items() if v is not None},
        )


class MemoryAuditSink(AuditSink):
    """把裁决攒在内存里（测试断言用）。"""

    def __init__(self) -> None:
        self.records: list[tuple[CommandVerdict, dict[str, Any]]] = []

    async def record(self, verdict: CommandVerdict, *, context: dict[str, Any]) -> None:
        """追加一条记录。"""
        self.records.append((verdict, dict(context)))

    def rule_ids(self) -> list[str]:
        """已记录的规则 ID 列表。"""
        return [v.rule_id or "" for v, _ in self.records]

    def clear(self) -> None:
        """清空。"""
        self.records.clear()


class DbAuditSink(AuditSink):
    """写入 ``audit_logs`` 表（生产实现）。

    走 ``async with UnitOfWork() as uow`` —— 与 WP1 冻结的写库姿势一致。
    审计失败**不得**影响主流程，因此这里吞掉异常并降级为日志。
    """

    async def record(self, verdict: CommandVerdict, *, context: dict[str, Any]) -> None:
        """追加一条 ``AuditLog``。"""
        from loopforge.db.uow import UnitOfWork  # noqa: PLC0415 - 避免执行层硬依赖 DB

        try:
            async with UnitOfWork() as uow:
                await uow.audit_logs.append(
                    action=f"executor.command.{verdict.decision.value.lower()}",
                    resource_type="command",
                    resource_id=context.get("step_id") or context.get("task_id"),
                    actor=context.get("actor", "executor"),
                    actor_type="system",
                    after=verdict.to_details(),
                    trace_id=context.get("trace_id"),
                    reason=verdict.reason,
                )
        except Exception as exc:  # noqa: BLE001 - 审计降级不阻塞执行
            _log.error("audit_sink_failed", error=str(exc), rule_id=verdict.rule_id)


# ---------------------------------------------------------------------------
# 策略引擎
# ---------------------------------------------------------------------------


class CommandPolicy:
    """命令白 / 黑名单裁决引擎。

    Args:
        deny_rules: 覆盖默认拒绝规则。
        approval_rules: 覆盖默认审批规则。
        argv0_allowlist: 允许模式下的程序白名单。
        allowlist_only: 为 ``True`` 时只放行白名单内的 ``argv[0]``。
        audit_sink: 审计出口，默认 :class:`NullAuditSink`。
        clock: 时间源。
        extra_rules: 追加的自定义规则（优先级高于内置）。

    Example:
        >>> policy = CommandPolicy()
        >>> policy.evaluate(["rm", "-rf", "/"]).decision.value
        'DENY'
        >>> policy.evaluate(["git", "status"]).decision.value
        'ALLOW'
    """

    def __init__(
        self,
        *,
        deny_rules: Sequence[CommandRule] | None = None,
        approval_rules: Sequence[CommandRule] | None = None,
        argv0_allowlist: Iterable[str] | None = None,
        allowlist_only: bool = False,
        audit_sink: AuditSink | None = None,
        clock: Clock | None = None,
        extra_rules: Sequence[CommandRule] = (),
    ) -> None:
        self._deny: tuple[CommandRule, ...] = tuple(
            deny_rules if deny_rules is not None else DEFAULT_DENY_RULES
        )
        self._approval: tuple[CommandRule, ...] = tuple(
            approval_rules if approval_rules is not None else DEFAULT_APPROVAL_RULES
        )
        self._extra: tuple[CommandRule, ...] = tuple(extra_rules)
        self._allowlist: frozenset[str] = frozenset(
            argv0_allowlist if argv0_allowlist is not None else DEFAULT_ARGV0_ALLOWLIST
        )
        self._allowlist_only: bool = allowlist_only
        self._audit: AuditSink = audit_sink or NullAuditSink()
        self._clock: Clock = clock or get_clock()

    # ------------------------------------------------------------------ 裁决

    def evaluate(self, argv: Sequence[str]) -> CommandVerdict:
        """对命令做裁决（纯函数，不抛异常、不写审计）。

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

        nul_hit = self._check_nul(items)
        if nul_hit is not None:
            return nul_hit

        joined = self._joined_text(items)
        program = self._program_name(items[0])

        for rule in (*self._extra, *self._deny):
            matched = rule.matches(items, joined)
            if matched is not None and rule.decision is PolicyDecision.DENY:
                return CommandVerdict(
                    decision=PolicyDecision.DENY,
                    rule_id=rule.rule_id,
                    reason=rule.reason,
                    matched=matched,
                    severity=rule.severity,
                    argv=items,
                )

        injection = self._detect_injection(items, program)
        if injection is not None:
            return injection

        if self._allowlist_only and program not in self._allowlist:
            return CommandVerdict(
                decision=PolicyDecision.DENY,
                rule_id="deny.not_in_allowlist",
                reason=f"程序 {program!r} 不在白名单内",
                matched=program,
                severity=Severity.ERROR,
                argv=items,
            )

        for rule in (*self._extra, *self._approval):
            matched = rule.matches(items, joined)
            if matched is not None and rule.decision is PolicyDecision.REQUIRE_APPROVAL:
                return CommandVerdict(
                    decision=PolicyDecision.REQUIRE_APPROVAL,
                    rule_id=rule.rule_id,
                    reason=rule.reason,
                    matched=matched,
                    severity=rule.severity,
                    argv=items,
                )

        return CommandVerdict(decision=PolicyDecision.ALLOW, argv=items)

    async def enforce(
        self, argv: Sequence[str], *, context: dict[str, Any] | None = None
    ) -> CommandVerdict:
        """裁决 + 审计 + 抛异常，供 Executor 直接调用。

        Args:
            argv: 命令数组。
            context: 审计上下文（run_id / task_id / step_id / trace_id / cwd）。

        Returns:
            ``ALLOW`` 的裁决结果。

        Raises:
            PolicyDeniedError: 命中拒绝规则（``LF-POLICY-1001``）。
            ApprovalRequiredError: 命中审批规则（``LF-POLICY-1002``）。
        """
        verdict = self.evaluate(argv)
        if verdict.allowed:
            return verdict

        ctx = dict(context or {})
        ctx.setdefault("checked_at", self._clock.now().isoformat())
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

    # ------------------------------------------------------------------ 规则管理

    def add_rule(self, rule: CommandRule) -> None:
        """追加一条自定义规则（优先级高于内置规则）。

        Args:
            rule: 规则。
        """
        self._extra = (*self._extra, rule)

    def rules(self) -> list[CommandRule]:
        """返回当前全部规则（自定义 → 拒绝 → 审批）。"""
        return [*self._extra, *self._deny, *self._approval]

    @property
    def audit_sink(self) -> AuditSink:
        """当前审计出口。"""
        return self._audit

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _check_nul(items: Sequence[str]) -> CommandVerdict | None:
        """检测参数中的 NUL 字节（截断攻击）。"""
        for index, item in enumerate(items):
            if "\x00" in item:
                return CommandVerdict(
                    decision=PolicyDecision.DENY,
                    rule_id="deny.nul_byte",
                    reason=f"argv[{index}] 含 NUL 字节",
                    matched=repr(item)[:80],
                    severity=Severity.CRITICAL,
                    argv=list(items),
                )
        return None

    @staticmethod
    def _joined_text(items: Sequence[str]) -> str:
        """把 argv 拼成用于**匹配**的单行文本。

        注意：这个字符串**只用于正则匹配，绝不回灌给 shell**。
        用空格拼接（而非 shlex.join 的引号形式），因为加引号会让
        ``rm -rf /`` 变成 ``rm -rf '/'``，反而绕过规则。
        """
        return " ".join(items)

    @staticmethod
    def _program_name(argv0: str) -> str:
        """从 ``argv[0]`` 提取程序名（去路径、去 ``.exe``）。"""
        head = argv0.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
        return head[:-4] if head.endswith(".exe") else head

    def _detect_injection(self, items: Sequence[str], program: str) -> CommandVerdict | None:
        """检测参数注入。

        由于全仓禁用 ``shell=True``，元字符本身对普通程序无害（会被当字面量）。
        真正危险的是 ``bash -c "<payload>"`` 这一族：payload 会被子 shell 解释。
        因此策略是：**当 argv[0] 是 shell 类程序时，对每个参数做二次拆解与全量规则重扫**。

        Args:
            items: 命令数组。
            program: 规范化后的程序名。

        Returns:
            命中时返回 ``DENY`` 裁决，否则 ``None``。
        """
        if program not in SHELL_BINARIES:
            return None
        for arg in items[1:]:
            if not any(meta in arg for meta in SHELL_METACHARACTERS):
                continue
            for segment in self._split_shell_payload(arg):
                if not segment:
                    continue
                inner = shlex.split(segment, posix=True) if segment.strip() else []
                inner_joined = segment
                for rule in (*self._extra, *self._deny):
                    matched = rule.matches(inner or [segment], inner_joined)
                    if matched is not None and rule.decision is PolicyDecision.DENY:
                        return CommandVerdict(
                            decision=PolicyDecision.DENY,
                            rule_id=rule.rule_id,
                            reason=f"shell 参数注入：{rule.reason}",
                            matched=matched,
                            severity=Severity.CRITICAL,
                            argv=list(items),
                        )
        return None

    @staticmethod
    def _split_shell_payload(payload: str) -> list[str]:
        """按 shell 分隔符切开 payload，逐段送去匹配。

        Args:
            payload: ``bash -c`` 后面那段脚本。

        Returns:
            子命令片段列表（含原始整串，避免跨段规则漏检）。
        """
        parts = re.split(r"(?:&&|\|\||;|\||\n|`|\$\()", payload)
        cleaned = [segment.strip().strip("\"'") for segment in parts]
        return [payload, *[item for item in cleaned if item]]
