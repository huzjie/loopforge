"""工具注册表 —— 注册 / 查询 / 角色边界 / JSON Schema 导出。

冻结接口（``TASK_BREAKDOWN.md`` §11.5）::

    class ToolRegistry:
        def register(self, tool: BaseTool) -> None: ...
        def get(self, name: str) -> BaseTool: ...                       # 未注册抛 LF-TOOL-0903
        def for_role(self, role: RoleName) -> list[BaseTool]: ...        # 角色工具边界过滤
        def to_specs(self, role: RoleName) -> list[ToolSpec]: ...

设计
----
* 内部复用 :class:`~loopforge.core.registry.Registry` 做名称管理，
  保证与 Provider / Retriever 注册表行为一致。
* **约定式自动发现**（§13.3）：:meth:`autoload_builtins` 扫描 ``tools`` 包，
  找到 ``BaseTool`` 的具体子类并注册 —— ``tools/__init__.py`` 只调这一行，
  新加工具 = 只新建自己的文件，零公共文件改动。
* 角色边界：工具可声明 ``allowed_roles``（``ClassVar[tuple[RoleName, ...]]``），
  未声明 = 所有角色可用；:meth:`for_role` 按此过滤。
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from loopforge.core.enums import RoleName
from loopforge.core.errors import ToolNotRegisteredError
from loopforge.core.registry import Registry
from loopforge.providers.types import ToolSpec
from loopforge.tools.base import BaseTool

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from loopforge.tools import base as _tools_base

__all__ = ["ToolRegistry", "default_role_tools"]


#: 默认角色工具边界：未在工具上声明 ``allowed_roles`` 时，按角色给的兜底集合。
#: 键为 ``RoleName``，值为工具名集合；``None`` 表示「全部工具」。
def default_role_tools() -> dict[RoleName, set[str] | None]:
    """构造默认角色 → 工具边界映射。

    Returns:
        角色到工具名集合的映射（``None`` 表示不限制）。
    """
    return {
        RoleName.PLANNER: {
            "read_file",
            "list_dir",
            "search_code",
            "run_tests",
            "git_ops",
            "http_fetch",
        },
        RoleName.CODER: None,  # 编码者全量工具
        RoleName.REVIEWER: {
            "read_file",
            "list_dir",
            "search_code",
            "run_tests",
            "git_ops",
        },
        RoleName.TESTER: {
            "read_file",
            "list_dir",
            "search_code",
            "run_tests",
            "run_shell",
            "git_ops",
        },
        RoleName.SUMMARIZER: {"read_file", "list_dir", "search_code"},
    }


class ToolRegistry:
    """工具注册表。

    Args:
        role_tools: 角色工具边界；``None`` 用默认映射。
    """

    def __init__(
        self,
        *,
        role_tools: dict[RoleName, set[str] | None] | None = None,
    ) -> None:
        self._registry: Registry[BaseTool] = Registry("tool")
        self._role_tools: dict[RoleName, set[str] | None] = (
            role_tools if role_tools is not None else default_role_tools()
        )

    # ------------------------------------------------------------------ 注册

    def register(self, tool: BaseTool) -> None:
        """注册一个工具实例。

        Args:
            tool: 工具实例。

        Raises:
            loopforge.core.errors.RegistryError: 名称冲突。
        """
        self._registry.register(tool.name, tool)

    def register_class(self, tool_cls: type[BaseTool]) -> BaseTool:
        """按类实例化并注册。

        Args:
            tool_cls: 工具类（无参构造）。

        Returns:
            注册后的实例。
        """
        tool = tool_cls()
        self.register(tool)
        return tool

    def unregister(self, name: str) -> None:
        """注销（测试用）。"""
        self._registry.unregister(name)

    # ------------------------------------------------------------------ 查询

    def get(self, name: str) -> BaseTool:
        """按名称取工具。

        Args:
            name: 工具名。

        Returns:
            工具实例。

        Raises:
            ToolNotRegisteredError: 未注册（``LF-TOOL-0903``）。
        """
        try:
            return self._registry.get(name)
        except Exception as exc:  # noqa: BLE001 - 统一转成冻结错误
            raise ToolNotRegisteredError(
                f"工具未注册：{name}",
                details={"tool_name": name, "available": self.list_names()},
            ) from exc

    def get_or_none(self, name: str) -> BaseTool | None:
        """取工具，不存在返回 ``None``。"""
        return self._registry.get_or_default(name)

    def list_names(self) -> list[str]:
        """已注册工具名（字典序）。"""
        return self._registry.list_names()

    def all(self) -> list[BaseTool]:
        """全部已注册工具。"""
        return [tool for _, tool in self._registry.items()]

    def __contains__(self, name: object) -> bool:
        """支持 ``"read_file" in registry``。"""
        return name in self._registry

    def __len__(self) -> int:
        """工具数量。"""
        return len(self._registry)

    # ------------------------------------------------------------------ 角色边界

    def for_role(self, role: RoleName) -> list[BaseTool]:
        """按角色过滤工具。

        Args:
            role: 角色。

        Returns:
            该角色可用工具列表（按名称字典序）。
        """
        allowed = self._role_tools.get(role)
        result: list[BaseTool] = []
        for tool in self.all():
            cls_allowed = getattr(type(tool), "allowed_roles", None)
            if cls_allowed is not None and role not in cls_allowed:
                continue
            if allowed is not None and tool.name not in allowed:
                continue
            result.append(tool)
        result.sort(key=lambda t: t.name)
        return result

    def set_role_tools(self, role: RoleName, names: set[str] | None) -> None:
        """覆盖某个角色的工具边界。

        Args:
            role: 角色。
            names: 工具名集合；``None`` 表示不限制。
        """
        self._role_tools[role] = names

    # ------------------------------------------------------------------ 导出

    def to_specs(self, role: RoleName) -> list[ToolSpec]:
        """导出该角色可见工具的 JSON Schema 声明（LLM function calling）。"""
        return [tool.to_spec() for tool in self.for_role(role)]

    def to_json_schema(self, role: RoleName) -> list[dict[str, Any]]:
        """导出成 OpenAI 风格 function 数组。

        Returns:
            ``[{"type": "function", "function": {"name", "description", "parameters"}}]``。
        """
        result: list[dict[str, Any]] = []
        for spec in self.to_specs(role):
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                    },
                }
            )
        return result

    # ------------------------------------------------------------------ 自动发现

    def autoload_builtins(self, package: str = "loopforge.tools") -> list[str]:
        """扫描包内模块，自动注册 ``BaseTool`` 具体子类。

        Args:
            package: 要扫描的包名。

        Returns:
            成功注册的工具名列表。
        """
        loaded: list[str] = []
        package_obj = importlib.import_module(package)
        for module_info in pkgutil.iter_modules(package_obj.__path__):
            if module_info.name.startswith("_"):
                continue
            module = importlib.import_module(f"{package}.{module_info.name}")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseTool)
                    and attr is not BaseTool
                    and getattr(attr, "abstract", False) is False
                    and attr.__module__ == module.__name__
                ):
                    try:
                        tool = attr()
                    except TypeError:
                        continue
                    if not tool.name:
                        continue
                    self._registry.register(tool.name, tool, override=False)
                    loaded.append(tool.name)
        return sorted(loaded)
