"""``tools`` 包 —— Agent 工具集。

约定式自动发现（§13.3）
----------------------
本文件**只做再导出**，不做任何注册逻辑 —— 新工具 = 只新建自己的文件，
零公共文件改动。需要注册表时：:

    registry = ToolRegistry()
    registry.autoload_builtins()          # 扫描本包，自动注册全部内置工具
    dispatcher = ToolDispatcher(registry)
"""

from __future__ import annotations

from loopforge.tools.base import (  # noqa: F401
    BaseTool,
    ProbeResult,
    StepIntent,
    ToolContext,
    ToolPreview,
    ToolResult,
)
from loopforge.tools.dispatcher import ToolDispatcher  # noqa: F401
from loopforge.tools.registry import ToolRegistry  # noqa: F401

__all__ = [
    "BaseTool",
    "ToolContext",
    "ToolResult",
    "ToolPreview",
    "ProbeResult",
    "StepIntent",
    "ToolRegistry",
    "ToolDispatcher",
    "default_registry",
    "default_dispatcher",
]


def default_registry() -> ToolRegistry:
    """构造并自动加载全部内置工具的注册表。

    Returns:
        已加载内置工具的 :class:`ToolRegistry`。
    """
    registry = ToolRegistry()
    registry.autoload_builtins()
    return registry


def default_dispatcher() -> ToolDispatcher:
    """构造默认分发器。

    Returns:
        绑定默认注册表的 :class:`ToolDispatcher`。
    """
    return ToolDispatcher(default_registry())
