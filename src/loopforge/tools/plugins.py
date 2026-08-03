"""工具插件加载 —— 第三方扩展与内置走同一条路径（§13.3 附带收益）。

入口点约定
----------
第三方包在 ``pyproject.toml`` 中声明::

    [project.entry-points."loopforge.tools"]
    my_tool = "my_package.tools:MyTool"

:func:`load_plugins` 通过 :class:`~loopforge.core.registry.Registry` 的
``load_entry_points`` 加载并注册到 :class:`~loopforge.tools.registry.ToolRegistry`。
任何插件异常都会被包装成 ``RegistryError``，**绝不静默吞掉** —— 插件是可信
边界之外，加载失败必须可见。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.errors import ConfigError
from loopforge.core.logging import get_logger
from loopforge.tools.base import BaseTool
from loopforge.tools.registry import ToolRegistry

__all__ = ["TOOL_ENTRY_POINT_GROUP", "load_plugins"]

_log = get_logger(__name__)

#: 工具插件的 entry-point 组名。
TOOL_ENTRY_POINT_GROUP: str = "loopforge.tools"


def load_plugins(
    registry: ToolRegistry,
    *,
    entry_point_group: str = TOOL_ENTRY_POINT_GROUP,
    override: bool = False,
) -> list[str]:
    """从 entry-points 加载第三方工具并注册。

    Args:
        registry: 目标工具注册表。
        entry_point_group: entry-point 组名。
        override: 是否允许插件覆盖内置工具。

    Returns:
        成功加载的工具名列表。

    Raises:
        ConfigError: 插件加载失败（由底层 :class:`Registry` 抛出并包装）。
    """
    # 复用 core Registry 的 entry-point 逻辑，但按工具实例注册。
    loaded: list[str] = []
    from importlib.metadata import EntryPoint, entry_points

    discovered: tuple[EntryPoint, ...] = tuple(entry_points(group=entry_point_group))
    for entry in discovered:
        try:
            obj = entry.load()
        except Exception as exc:  # noqa: BLE001 - 插件任何异常都可见
            raise ConfigError(
                f"加载工具插件 {entry.name!r} 失败：{exc}",
                details={"plugin": entry.name, "error": str(exc)},
            ) from exc
        tool = _coerce_tool(obj, entry.name)
        if tool is None:
            continue
        try:
            registry.register(tool)
        except Exception as exc:  # noqa: BLE001 - 注册冲突可见
            if override:
                registry.unregister(tool.name)
                registry.register(tool)
            else:
                raise ConfigError(
                    f"工具插件 {entry.name!r} 注册失败（名称冲突：{tool.name}）",
                    details={"plugin": entry.name, "tool_name": tool.name},
                ) from exc
        loaded.append(tool.name)
        _log.info("tool_plugin_loaded", plugin=entry.name, tool=tool.name)
    return loaded


def _coerce_tool(obj: Any, plugin_name: str) -> BaseTool | None:
    """把 entry-point 加载的对象规范成工具实例。

    Args:
        obj: entry-point 返回值（类或实例）。
        plugin_name: 插件名（日志用）。

    Returns:
        工具实例；无法识别时返回 ``None``。
    """
    if isinstance(obj, BaseTool):
        return obj
    if isinstance(obj, type) and issubclass(obj, BaseTool) and obj is not BaseTool:
        return obj()
    _log.warning("tool_plugin_invalid", plugin=plugin_name)
    return None
