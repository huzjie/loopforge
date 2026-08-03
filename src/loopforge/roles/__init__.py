"""``loopforge.roles`` —— 多角色 Agent 层（WP6）。

角色注册表（§13.3 约定式自动发现）
----------------------------------
``roles/__init__.py`` **只做再导出，不写注册逻辑**。需要注册表时::

    from loopforge.roles import role_registry
    role_registry()                      # 扫描本包，自动注册五个角色类
    cls = role_registry().get("coder")   # → CoderRole

五个角色（§11.9）:
    * PLANNER     —— 规划：Run goal → TaskDraft[] + 里程碑 + Pinned Constraints
    * CODER       —— 编码：读码 → 改码 → 自查（拥有写工具）
    * REVIEWER    —— 评审：只读，产出 ReviewVerdict（**零写权限**）
    * TESTER      —— 测试：补测试、跑验证、失败归因
    * SUMMARIZER  —— 总结：纯文本角色（**不调工具**）

下游（WP4）标准用法::

    from loopforge.roles import RoleRouter, create_role, role_registry
    router = RoleRouter()
    role_name = router.select(phase="implement", task_kind="code")
    role = create_role(role_name, provider=provider)   # 绑定按 config.roles 取
    output = await role.act(ctx)
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

from loopforge.core.enums import RoleName
from loopforge.core.registry import Registry
from loopforge.roles.base import (
    FROZEN_ROLE_TOOLS,
    BaseRole,
    MilestoneDraft,
    RoleBindingLike,
    RoleContext,
    RoleOutput,
    TaskDraft,
    assert_role_can_tool,
    can_role_use_tool,
    publish_tool_denied,
    publish_tool_denied_async,
    role_tool_names,
)
from loopforge.roles.coder import CodeChangeResult, CoderRole
from loopforge.roles.planner import PlanResult, PlannerRole
from loopforge.roles.prompts import (
    PROMPT_NAMES,
    PROMPTS_DIR,
    PromptTemplate,
    RenderedPrompt,
    get_prompts_dir,
    load_prompt,
    prompt_hash,
    prompt_versions,
    render_prompt,
    reset_prompt_cache,
    validate_placeholders,
)
from loopforge.roles.reviewer import ReviewFinding, ReviewVerdict, ReviewerRole
from loopforge.roles.router import IMPLEMENT_PHASES, PHASE_ROLE_DEFAULT, RoleRouter
from loopforge.roles.summarizer import (
    ConstraintFidelity,
    PrDescription,
    SummarizerRole,
    SummaryResult,
)
from loopforge.roles.tester import TestFailure, TestReport, TesterRole

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注
    from loopforge.providers.base import BaseProvider

__all__ = [
    # base
    "BaseRole",
    "RoleContext",
    "RoleOutput",
    "TaskDraft",
    "MilestoneDraft",
    "RoleBindingLike",
    "FROZEN_ROLE_TOOLS",
    "role_tool_names",
    "can_role_use_tool",
    "assert_role_can_tool",
    "publish_tool_denied",
    "publish_tool_denied_async",
    # prompts
    "PROMPT_NAMES",
    "PROMPTS_DIR",
    "PromptTemplate",
    "RenderedPrompt",
    "get_prompts_dir",
    "load_prompt",
    "render_prompt",
    "prompt_versions",
    "prompt_hash",
    "reset_prompt_cache",
    "validate_placeholders",
    # 角色
    "PlannerRole",
    "CoderRole",
    "ReviewerRole",
    "TesterRole",
    "SummarizerRole",
    "PlanResult",
    "CodeChangeResult",
    "ReviewVerdict",
    "ReviewFinding",
    "TestReport",
    "TestFailure",
    "SummaryResult",
    "PrDescription",
    "ConstraintFidelity",
    # 路由
    "RoleRouter",
    "PHASE_ROLE_DEFAULT",
    "IMPLEMENT_PHASES",
    # 注册表
    "role_registry",
    "create_role",
    "create_role_from_settings",
]

#: 角色类注册表（惰性填充；名称为小写角色名）。
_role_registry: Registry[type[BaseRole]] | None = None


def role_registry() -> Registry[type[BaseRole]]:
    """构造（并缓存）角色注册表 —— 扫描本包自动注册五个角色类。

    Returns:
        已注册全部内置角色的 :class:`~loopforge.core.registry.Registry`。
    """
    global _role_registry
    if _role_registry is not None:
        return _role_registry
    registry: Registry[type[BaseRole]] = Registry("role")
    package = importlib.import_module(__name__)
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseRole)
                and attr is not BaseRole
                and getattr(attr, "abstract", False) is False
                and attr.__module__ == module.__name__
            ):
                try:
                    role_name = attr.name.value.lower()
                except Exception:  # noqa: BLE001 - 非角色类跳过
                    continue
                registry.register(role_name, attr, override=False)
    _role_registry = registry
    return registry


def create_role(role: RoleName, provider: "BaseProvider", *, binding: RoleBindingLike | None = None) -> BaseRole:
    """按角色名实例化角色。

    Args:
        role: 角色名。
        provider: 已实例化的 Provider。
        binding: 角色模型绑定；``None`` 时从
            :func:`loopforge.core.config.get_settings().roles.for_role` 构造。

    Returns:
        角色实例。

    Raises:
        loopforge.core.errors.RegistryError: 角色未注册。
    """
    if binding is None:
        from loopforge.core.config import get_settings

        cfg = get_settings().roles.for_role(role.value)
        binding = RoleBindingLike(
            provider=cfg.provider,
            model=cfg.model,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )
    cls = role_registry().get(role.value)
    return cls(binding=binding, provider=provider)


def create_role_from_settings(role: RoleName, provider: "BaseProvider") -> BaseRole:
    """便捷别名：按全局配置绑定实例化角色。"""
    return create_role(role, provider=provider)
