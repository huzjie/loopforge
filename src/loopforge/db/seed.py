"""首次启动的种子数据。

来源：``docs/TASK_BREAKDOWN.md`` §3.1.3「seed：默认 provider / 角色 / 策略 / 预算」。

**幂等是硬要求**：``loopforge db seed`` 会在每次容器启动时被调用，重复执行必须
是无害的。因此所有写入都走「查得到就更新、查不到才插入」，绝不 blind INSERT。

种子内容：

============  =========================================================
表            内容
============  =========================================================
provider_configs  配置里 ``providers.default`` 与故障转移链上的全部 provider
agent_roles       5 个 :class:`~loopforge.core.enums.RoleName` 的模型绑定
policies          一份内置的 ``default`` 安全基线策略（8 条规则）
budgets           ORG 级总预算 + 当日 DAY 预算
users / api_keys  仅当配置里给了 bootstrap 凭据时才创建管理员
============  =========================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from loopforge.core.config import Settings, get_settings
from loopforge.core.enums import BudgetScope, PolicyDecision, RoleName
from loopforge.core.hashing import sha256_hex
from loopforge.core.logging import get_logger
from loopforge.db.mixins import utcnow
from loopforge.db.uow import UnitOfWork

__all__ = [
    "SeedReport",
    "DEFAULT_POLICY_NAME",
    "DEFAULT_POLICY_RULES",
    "default_policy_yaml",
    "seed_providers",
    "seed_agent_roles",
    "seed_default_policy",
    "seed_budgets",
    "seed_bootstrap_admin",
    "seed_all",
]

_logger = get_logger(__name__)

#: 内置基线策略的名字。升级策略请发布新 version，不要改名。
DEFAULT_POLICY_NAME: str = "default"

#: 内置基线策略的规则表 —— 顺序即求值顺序，首个命中即裁决。
#:
#: 设计原则：**默认拒绝高危、放行只读**。宁可多弹几次审批，也不能让智能体
#: 在无人值守的 16 天里把生产分支 force push 掉。
DEFAULT_POLICY_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "R-001-deny-secret-paths",
        "category": "path",
        "match": {"path_globs": ["**/.env", "**/*.pem", "**/id_rsa*", "**/.ssh/**"]},
        "effect": PolicyDecision.DENY,
        "order": 0,
        "reason": "禁止读写密钥与凭据文件",
    },
    {
        "rule_id": "R-002-deny-outside-workspace",
        "category": "path",
        "match": {"outside_workspace": True},
        "effect": PolicyDecision.DENY,
        "order": 1,
        "reason": "文件操作不得越出 Run 的工作区沙箱",
    },
    {
        "rule_id": "R-003-deny-destructive-shell",
        "category": "tool",
        "match": {
            "tool": "shell",
            "command_regex": r"(?:^|\s)(rm\s+-rf\s+/|mkfs|dd\s+if=|shutdown|reboot|:\(\)\{)",
        },
        "effect": PolicyDecision.DENY,
        "order": 2,
        "reason": "禁止破坏性 shell 命令",
    },
    {
        "rule_id": "R-004-approval-force-push",
        "category": "vcs",
        "match": {"operation": "push", "force": True},
        "effect": PolicyDecision.REQUIRE_APPROVAL,
        "order": 3,
        "reason": "强制推送需人工确认",
    },
    {
        "rule_id": "R-005-deny-protected-branch-write",
        "category": "vcs",
        "match": {"operation": "push", "branch_globs": ["main", "master", "release/*"]},
        "effect": PolicyDecision.DENY,
        "order": 4,
        "reason": "受保护分支只能通过 PR 合入",
    },
    {
        "rule_id": "R-006-approval-network-egress",
        "category": "network",
        "match": {"host_not_in": ["localhost", "127.0.0.1", "pypi.org", "files.pythonhosted.org"]},
        "effect": PolicyDecision.REQUIRE_APPROVAL,
        "order": 5,
        "reason": "访问白名单之外的外网域名需人工确认",
    },
    {
        "rule_id": "R-007-approval-high-cost-call",
        "category": "cost",
        "match": {"single_call_usd_gt": 1.0},
        "effect": PolicyDecision.REQUIRE_APPROVAL,
        "order": 6,
        "reason": "单次调用成本超过 1 USD 需人工确认",
    },
    {
        "rule_id": "R-999-allow-default",
        "category": "tool",
        "match": {},
        "effect": PolicyDecision.ALLOW,
        "order": 999,
        "reason": "兜底放行（前面所有规则都没命中）",
    },
)


@dataclass(slots=True)
class SeedReport:
    """一次 seed 的结果汇总，供 CLI 打印与测试断言。"""

    providers: int = 0
    agent_roles: int = 0
    policies: int = 0
    budgets: int = 0
    users: int = 0
    api_keys: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """写入 / 更新的记录总数。"""
        return (
            self.providers
            + self.agent_roles
            + self.policies
            + self.budgets
            + self.users
            + self.api_keys
        )

    def as_dict(self) -> dict[str, Any]:
        """转成可 JSON 化的字典。"""
        return {
            "providers": self.providers,
            "agent_roles": self.agent_roles,
            "policies": self.policies,
            "budgets": self.budgets,
            "users": self.users,
            "api_keys": self.api_keys,
            "skipped": list(self.skipped),
            "total": self.total,
        }


def default_policy_yaml() -> str:
    """渲染内置基线策略的 YAML 源文本。

    之所以在代码里手工拼 YAML 而不是 ``yaml.safe_dump``：``checksum`` 是
    ``yaml_source`` 的 sha256，必须**逐字节稳定**。第三方序列化器的换行、
    引号、键顺序会随版本变化，那样每次升级依赖都会误报「策略被篡改」。

    :returns: 稳定的 YAML 文本。
    """
    lines: list[str] = [
        f"name: {DEFAULT_POLICY_NAME}",
        "description: LoopForge 内置安全基线（默认拒绝高危，放行只读）",
        "rules:",
    ]
    for rule in DEFAULT_POLICY_RULES:
        lines.append(f"  - rule_id: {rule['rule_id']}")
        lines.append(f"    category: {rule['category']}")
        lines.append(f"    effect: {PolicyDecision(rule['effect']).value}")
        lines.append(f"    order: {rule['order']}")
        lines.append(f"    reason: {rule['reason']}")
        match = rule["match"]
        if not match:
            lines.append("    match: {}")
        else:
            lines.append("    match:")
            for key in sorted(match):
                lines.append(f"      {key}: {match[key]!r}")
    return "\n".join(lines) + "\n"


async def seed_providers(uow: UnitOfWork, settings: Settings) -> int:
    """写入 provider 配置。

    只落配置里真正会被用到的 provider —— ``providers.default`` 加上
    ``providers.failover_chain``，避免种一堆用不上的空壳记录。

    :returns: upsert 的条数。
    """
    names: list[str] = [settings.providers.default]
    for name in settings.providers.failover_chain:
        if name not in names:
            names.append(name)

    count = 0
    for index, name in enumerate(names):
        failover_to = names[index + 1] if index + 1 < len(names) else None
        await uow.providers.upsert(
            name,
            kind="mock" if name == "mock" else "openai_compatible",
            base_url=None,
            api_key_enc=None,
            default_model="mock-large" if name == "mock" else None,
            options={
                "request_timeout_s": settings.providers.request_timeout_s,
                "max_retries": settings.providers.max_retries,
                "stream_enabled": settings.providers.stream_enabled,
            },
            enabled=True,
            priority=(index + 1) * 10,
            failover_to=failover_to,
        )
        count += 1
    return count


async def seed_agent_roles(uow: UnitOfWork, settings: Settings) -> int:
    """按配置里的 ``roles.*`` 绑定写入 5 个角色。

    :returns: upsert 的条数。
    """
    bindings = {
        RoleName.PLANNER: settings.roles.planner,
        RoleName.CODER: settings.roles.coder,
        RoleName.REVIEWER: settings.roles.reviewer,
        RoleName.TESTER: settings.roles.tester,
        RoleName.SUMMARIZER: settings.roles.summarizer,
    }
    #: 各角色的默认工具白名单 —— 最小权限原则。
    tool_allowlist: dict[RoleName, list[str]] = {
        RoleName.PLANNER: ["fs.read", "search.code", "memory.query"],
        RoleName.CODER: ["fs.read", "fs.write", "shell", "search.code", "vcs.commit"],
        RoleName.REVIEWER: ["fs.read", "search.code", "vcs.diff"],
        RoleName.TESTER: ["fs.read", "shell", "test.run"],
        RoleName.SUMMARIZER: ["memory.query", "memory.write"],
    }

    count = 0
    for role, binding in bindings.items():
        provider = await uow.providers.get_by_name(binding.provider)
        await uow.agent_roles.upsert(
            role,
            provider_config_id=provider.id if provider is not None else None,
            model=binding.model,
            temperature=binding.temperature,
            max_tokens=binding.max_tokens,
            allowed_tools=tool_allowlist[role],
            prompt_version="v1",
            enabled=True,
        )
        count += 1
    return count


async def seed_default_policy(uow: UnitOfWork, settings: Settings) -> int:
    """发布内置基线策略（已存在同内容版本则跳过）。

    若 ``governance.policy_file`` 指向的 YAML 存在，则以文件内容为准；
    否则用 :func:`default_policy_yaml` 的内置版本。

    :returns: 新发布的策略数（0 表示无变化）。
    """
    policy_path: Path = settings.governance.policy_file
    if not policy_path.is_absolute():
        policy_path = Path.cwd() / policy_path

    if policy_path.is_file():
        yaml_source = policy_path.read_text(encoding="utf-8")
    else:
        yaml_source = default_policy_yaml()

    checksum = sha256_hex(yaml_source)
    active = await uow.policies.get_active(DEFAULT_POLICY_NAME)
    if active is not None and active.checksum == checksum:
        return 0

    await uow.policies.publish(
        name=DEFAULT_POLICY_NAME,
        yaml_source=yaml_source,
        compiled={"rules": [dict(rule) for rule in DEFAULT_POLICY_RULES]},
        rules=[dict(rule) for rule in DEFAULT_POLICY_RULES],
        activate=True,
    )
    return 1


async def seed_budgets(
    uow: UnitOfWork, settings: Settings, *, now: datetime | None = None
) -> int:
    """创建 ORG 级总预算与当日 DAY 预算。

    :returns: 新建的预算条数（已存在的不重复创建，也不覆盖已消耗额度）。
    """
    moment = now if now is not None else utcnow()
    day_ref = moment.date().isoformat()
    budget_cfg = settings.governance.budget

    specs: tuple[tuple[BudgetScope, str, str, Decimal, datetime | None], ...] = (
        (
            BudgetScope.ORG,
            "default",
            "monthly",
            Decimal(str(budget_cfg.default_day_limit_usd)) * 30,
            moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        ),
        (
            BudgetScope.DAY,
            day_ref,
            "daily",
            Decimal(str(budget_cfg.default_day_limit_usd)),
            moment.replace(hour=0, minute=0, second=0, microsecond=0),
        ),
    )

    count = 0
    for scope, scope_ref, window, limit_usd, window_start in specs:
        existing = await uow.budgets.get_for_scope(scope, scope_ref, window=window)
        if existing is not None:
            continue
        await uow.budgets.create(
            scope=scope,
            scope_ref=scope_ref,
            window=window,
            limit_usd=limit_usd,
            warn_at=budget_cfg.warn_at,
            throttle_at=budget_cfg.throttle_at,
            halt_at=budget_cfg.halt_at,
            window_start=window_start,
            active=True,
        )
        count += 1
    return count


async def seed_bootstrap_admin(
    uow: UnitOfWork, settings: Settings
) -> tuple[int, int, str | None]:
    """创建 bootstrap 管理员与其 API Key。

    **只有在配置显式提供了 bootstrap 凭据时才执行**。绝不生成「弱默认密码」——
    自托管平台最常见的失陷路径就是没人改过的默认口令。

    :returns: ``(新建用户数, 新建 Key 数, 跳过原因)``；成功时第三个元素为 ``None``。
    """
    bootstrap = settings.security.bootstrap_key.strip()
    if not bootstrap:
        return 0, 0, f"未设置 {settings.security.default_admin_key_env}，跳过管理员初始化"

    username = "admin"
    hashed_password = sha256_hex(f"lf-bootstrap:{bootstrap}")
    user, created = await uow.users.ensure_user(
        username=username, hashed_password=hashed_password, role="admin"
    )
    user_count = 1 if created else 0

    prefix = f"{settings.security.api_key_prefix}_{sha256_hex(bootstrap)[:12]}"
    existing_key = await uow.api_keys.get_by_prefix(prefix)
    if existing_key is not None:
        return user_count, 0, None

    await uow.api_keys.issue(
        prefix=prefix,
        hashed_secret=sha256_hex(bootstrap),
        name="bootstrap",
        scopes=["*"],
        user_id=user.id,
        ttl_days=settings.security.api_key_ttl_days,
    )
    return user_count, 1, None


async def seed_all(
    *,
    settings: Settings | None = None,
    uow: UnitOfWork | None = None,
) -> SeedReport:
    """执行全部 seed 步骤（幂等）。

    :param settings: 配置；``None`` 表示取全局 :func:`~loopforge.core.config.get_settings`。
    :param uow: 复用外部事务；``None`` 表示自建一个 UoW 并在结束时提交。
    :returns: :class:`SeedReport`。
    """
    conf = settings if settings is not None else get_settings()
    report = SeedReport()

    async def _run(active_uow: UnitOfWork) -> None:
        report.providers = await seed_providers(active_uow, conf)
        report.agent_roles = await seed_agent_roles(active_uow, conf)
        report.policies = await seed_default_policy(active_uow, conf)
        report.budgets = await seed_budgets(active_uow, conf)
        users, keys, skipped = await seed_bootstrap_admin(active_uow, conf)
        report.users = users
        report.api_keys = keys
        if skipped is not None:
            report.skipped.append(skipped)

    if uow is not None:
        await _run(uow)
    else:
        async with UnitOfWork() as owned:
            await _run(owned)

    _logger.info("db.seed.completed", **report.as_dict())
    return report
