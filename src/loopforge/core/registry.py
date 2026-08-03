"""泛型注册表 —— Provider / Tool / Executor / Retriever / Role 五处复用。

设计要点（``TASK_BREAKDOWN`` §13.3 冲突规避）：
    各 WP **不得**各写一份注册表，统一复用 :class:`Registry`，
    这样 ``providers/__init__.py`` 之类的初始化文件只需要调 ``register()``，
    多人并行时冲突面缩到一行。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Generic, TypeVar

from loopforge.core.errors import ConfigError, LoopForgeError

__all__ = ["Registry", "RegistryError"]

T = TypeVar("T")


class RegistryError(LoopForgeError):
    """注册表操作异常（名称冲突 / 未注册 / 插件加载失败）。"""

    code = "LF-CORE-0002"
    http_status = 500
    retryable = False


class Registry(Generic[T]):
    """名称 → 对象 的泛型注册表。

    典型用法::

        provider_registry: Registry[type[BaseProvider]] = Registry("provider")

        @provider_registry.decorator("openai")
        class OpenAIProvider(BaseProvider):
            ...

        cls = provider_registry.get("openai")

    Attributes:
        kind: 注册表种类名，出现在错误消息里（如 ``"provider"``）。
    """

    __slots__ = ("kind", "_items", "_aliases", "_frozen", "_entry_point_group")

    def __init__(
        self,
        kind: str,
        *,
        entry_point_group: str | None = None,
        initial: Mapping[str, T] | None = None,
    ) -> None:
        """初始化注册表。

        Args:
            kind: 种类名，例如 ``provider`` / ``tool``。
            entry_point_group: 第三方插件的 entry-point 组名，
                例如 ``loopforge.providers``；为 ``None`` 时不支持插件加载。
            initial: 初始注册项。

        Raises:
            ValueError: ``kind`` 为空。
        """
        if not kind or not kind.strip():
            raise ValueError("Registry.kind 不能为空")
        self.kind = kind.strip()
        self._items: dict[str, T] = {}
        self._aliases: dict[str, str] = {}
        self._frozen: bool = False
        self._entry_point_group = entry_point_group
        for name, item in (initial or {}).items():
            self.register(name, item)

    # ------------------------------------------------------------------ 写入

    def register(
        self,
        name: str,
        item: T,
        *,
        aliases: tuple[str, ...] = (),
        override: bool = False,
    ) -> T:
        """注册一个对象。

        Args:
            name: 唯一名称，大小写不敏感（内部统一小写）。
            item: 被注册对象（通常是类或工厂函数）。
            aliases: 别名列表。
            override: 是否允许覆盖同名项。

        Returns:
            ``item`` 本身，便于链式使用。

        Raises:
            RegistryError: 注册表已冻结 / 名称冲突 / 名称非法。
        """
        if self._frozen:
            raise RegistryError(
                f"{self.kind} 注册表已冻结，不能再注册 {name!r}",
                details={"kind": self.kind, "name": name},
            )
        key = self._normalize(name)
        if key in self._items and not override:
            raise RegistryError(
                f"{self.kind} 名称冲突：{name!r} 已被注册",
                details={"kind": self.kind, "name": name},
            )
        self._items[key] = item
        for alias in aliases:
            alias_key = self._normalize(alias)
            if alias_key in self._aliases and not override:
                raise RegistryError(
                    f"{self.kind} 别名冲突：{alias!r} 已指向 {self._aliases[alias_key]!r}",
                    details={"kind": self.kind, "alias": alias},
                )
            self._aliases[alias_key] = key
        return item

    def decorator(
        self, name: str, *, aliases: tuple[str, ...] = (), override: bool = False
    ) -> Callable[[T], T]:
        """以装饰器形式注册。

        Args:
            name: 唯一名称。
            aliases: 别名列表。
            override: 是否允许覆盖。

        Returns:
            装饰器函数。
        """

        def _wrap(item: T) -> T:
            self.register(name, item, aliases=aliases, override=override)
            return item

        return _wrap

    def unregister(self, name: str) -> None:
        """注销一个对象（测试与热插拔用）。

        Args:
            name: 名称或别名。

        Raises:
            RegistryError: 注册表已冻结或名称不存在。
        """
        if self._frozen:
            raise RegistryError(
                f"{self.kind} 注册表已冻结，不能注销 {name!r}",
                details={"kind": self.kind, "name": name},
            )
        key = self._resolve(name)
        if key is None:
            raise RegistryError(
                f"{self.kind} 未注册：{name!r}", details={"kind": self.kind, "name": name}
            )
        self._items.pop(key, None)
        for alias, target in list(self._aliases.items()):
            if target == key:
                self._aliases.pop(alias, None)

    def clear(self) -> None:
        """清空注册表（仅测试使用）。"""
        self._items.clear()
        self._aliases.clear()
        self._frozen = False

    def freeze(self) -> None:
        """冻结注册表，之后不再允许写入。"""
        self._frozen = True

    @property
    def frozen(self) -> bool:
        """是否已冻结。"""
        return self._frozen

    # ------------------------------------------------------------------ 读取

    def get(self, name: str) -> T:
        """按名称取出对象。

        Args:
            name: 名称或别名，大小写不敏感。

        Returns:
            注册的对象。

        Raises:
            RegistryError: 未注册。
        """
        key = self._resolve(name)
        if key is None:
            raise RegistryError(
                f"{self.kind} 未注册：{name!r}，已注册的有 {self.list_names()}",
                details={"kind": self.kind, "name": name, "available": self.list_names()},
            )
        return self._items[key]

    def get_or_default(self, name: str, default: T | None = None) -> T | None:
        """取对象，不存在时返回默认值。

        Args:
            name: 名称或别名。
            default: 缺省返回值。

        Returns:
            对象或 ``default``。
        """
        key = self._resolve(name)
        return self._items[key] if key is not None else default

    def list_names(self) -> list[str]:
        """列出所有已注册名称（不含别名），按字典序排序。

        Returns:
            名称列表。
        """
        return sorted(self._items)

    def list_aliases(self) -> dict[str, str]:
        """列出别名 → 正名的映射。

        Returns:
            别名字典副本。
        """
        return dict(self._aliases)

    def items(self) -> list[tuple[str, T]]:
        """列出 ``(名称, 对象)`` 对，按名称排序。

        Returns:
            列表。
        """
        return [(name, self._items[name]) for name in self.list_names()]

    def __contains__(self, name: object) -> bool:
        """支持 ``"openai" in registry``。"""
        return isinstance(name, str) and self._resolve(name) is not None

    def __len__(self) -> int:
        """已注册数量（不含别名）。"""
        return len(self._items)

    def __iter__(self) -> Iterator[str]:
        """迭代名称。"""
        return iter(self.list_names())

    def __repr__(self) -> str:
        """调试友好表示。"""
        return f"Registry(kind={self.kind!r}, size={len(self._items)}, frozen={self._frozen})"

    # ------------------------------------------------------------------ 插件

    def load_entry_points(self, *, override: bool = False) -> list[str]:
        """从 Python entry-points 加载第三方插件。

        Args:
            override: 是否允许插件覆盖内置实现。

        Returns:
            成功加载的名称列表。

        Raises:
            ConfigError: 未配置 ``entry_point_group``。
            RegistryError: 某个插件加载失败。
        """
        if self._entry_point_group is None:
            raise ConfigError(
                f"{self.kind} 注册表未配置 entry_point_group，无法加载插件",
                details={"kind": self.kind},
            )
        loaded: list[str] = []
        discovered: tuple[EntryPoint, ...] = tuple(
            entry_points(group=self._entry_point_group)  # type: ignore[call-overload]
        )
        for entry in discovered:
            try:
                obj = entry.load()
            except Exception as exc:  # noqa: BLE001 —— 插件任何异常都要转成注册表错误
                raise RegistryError(
                    f"加载 {self.kind} 插件 {entry.name!r} 失败：{exc}",
                    details={"kind": self.kind, "name": entry.name, "error": str(exc)},
                ) from exc
            self.register(entry.name, obj, override=override)
            loaded.append(entry.name)
        return loaded

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _normalize(name: str) -> str:
        """名称归一化：去空白 + 小写。

        Args:
            name: 原始名称。

        Returns:
            归一化后的键。

        Raises:
            RegistryError: 名称为空。
        """
        key = name.strip().lower()
        if not key:
            raise RegistryError("注册名不能为空", details={"name": name})
        return key

    def _resolve(self, name: str) -> str | None:
        """把名称或别名解析成正名键。

        Args:
            name: 名称或别名。

        Returns:
            正名键；不存在返回 ``None``。
        """
        if not isinstance(name, str) or not name.strip():
            return None
        key = name.strip().lower()
        if key in self._items:
            return key
        target = self._aliases.get(key)
        return target if target in self._items else None


def build_registry(kind: str, **kwargs: Any) -> Registry[Any]:
    """工厂函数：创建一个 ``Registry[Any]``。

    Args:
        kind: 种类名。
        **kwargs: 透传给 :class:`Registry` 的关键字参数。

    Returns:
        新建的注册表。
    """
    return Registry(kind, **kwargs)
