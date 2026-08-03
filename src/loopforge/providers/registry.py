"""Provider 注册与实例化 —— 名称 → 类映射 + 配置驱动构造。

职责
----
1. **内置注册**：把 :mod:`loopforge.providers` 下的具体 Provider 类注册进
   :class:`~loopforge.core.registry.Registry`。每个类同时注册三个键：
   ``name``（主键）、``kind``（配置里的 kind 值）、``aliases``（别名）；
2. **实例化**：从 :class:`ProviderConfigModel`（内存投影）构造活的 Provider；
3. **配置驱动**：从 ``settings.providers`` 与 DB ``provider_configs`` 行构造，
   解密动作属于调用方（WP7 安全层），本模块只接收**已解密**的 Key；
4. **插件加载**：可选地加载 ``loopforge.providers`` entry-point 组（默认关闭，
   CLI 显式开启时使用）。

新增 Provider 的约定（``TASK_BREAKDOWN`` §13.3）
-------------------------------------------------
实现类必须：
* 继承 :class:`~loopforge.providers.base.BaseProvider`；
* 声明 ``name``（小写唯一）、``kind``（配置 kind）与可选 ``aliases``；
* 放在 :mod:`loopforge.providers` 包内（本模块 :data:`_BUILTIN_MODULES`
  显式导入）。

之后**零公共文件改动**即可被本注册表发现。
"""

from __future__ import annotations

import importlib
from typing import Any

from loopforge.core.clock import Clock, get_clock
from loopforge.core.errors import ProviderError
from loopforge.core.logging import get_logger
from loopforge.core.registry import Registry
from loopforge.providers.types import ProviderConfigModel

__all__ = [
    "provider_registry",
    "get_provider_class",
    "create_provider",
    "create_mock_provider",
    "get_provider",
    "load_builtin_providers",
    "autoload_providers",
]

_LOG = get_logger(__name__)

#: 进程级 Provider 类注册表。
provider_registry: Registry[type[Any]] = Registry(
    "provider",
    entry_point_group="loopforge.providers",
)

#: 已导入过的内置子模块（防重复导入）。
_IMPORTED: set[str] = set()

#: 内置子模块清单 —— 导入以触发类定义，随后由 :func:`_register_builtin` 注册。
_BUILTIN_MODULES: tuple[str, ...] = (
    "loopforge.providers.mock",
    "loopforge.providers.openai_compat",
    "loopforge.providers.dashscope",
    "loopforge.providers.anthropic",
    "loopforge.providers.deepseek",
    "loopforge.providers.ollama",
)


def _import_builtin(module_name: str) -> None:
    """导入一个内置 Provider 子模块（幂等）。

    Args:
        module_name: 完整模块名。
    """
    if module_name in _IMPORTED:
        return
    try:
        importlib.import_module(module_name)
        _IMPORTED.add(module_name)
    except Exception as exc:  # noqa: BLE001 —— 单个模块失败不应拖垮注册表
        _LOG.warning("provider_module_import_failed", module=module_name, error=str(exc))


def _register_builtin(cls: type[Any]) -> None:
    """把 Provider 类注册进注册表（name / kind / aliases 三键）。

    Args:
        cls: Provider 类。
    """
    name = str(getattr(cls, "name", "")).strip()
    if not name:
        return
    kind = str(getattr(cls, "kind", "")).strip()
    aliases = tuple(str(a) for a in getattr(cls, "aliases", ()))
    extra_aliases: list[str] = list(aliases)
    if kind and kind != name and kind not in extra_aliases:
        extra_aliases.append(kind)
    provider_registry.register(name, cls, aliases=tuple(extra_aliases), override=True)


def load_builtin_providers() -> list[str]:
    """导入并注册全部内置 Provider，返回已注册名列表。

    Returns:
        注册的 Provider 名（字典序）。
    """
    for module_name in _BUILTIN_MODULES:
        _import_builtin(module_name)
    # 显式注册：把每个已导入模块中的具体 Provider 类挂进注册表。
    _register_builtin_classes()
    return provider_registry.list_names()


def _register_builtin_classes() -> None:
    """从已导入模块中取出 Provider 类并注册。"""
    mapping = (
        ("loopforge.providers.mock", "MockProvider"),
        ("loopforge.providers.openai_compat", "OpenAICompatProvider"),
        ("loopforge.providers.dashscope", "DashScopeProvider"),
        ("loopforge.providers.anthropic", "AnthropicProvider"),
        ("loopforge.providers.deepseek", "DeepSeekProvider"),
        ("loopforge.providers.ollama", "OllamaProvider"),
    )
    for module_name, class_name in mapping:
        try:
            module = importlib.import_module(module_name)
            cls = getattr(module, class_name)
        except (ImportError, AttributeError) as exc:  # pragma: no cover
            _LOG.warning(
                "provider_class_missing",
                module=module_name,
                class_name=class_name,
                error=str(exc),
            )
            continue
        _register_builtin(cls)


def autoload_providers(*, entry_points: bool = False) -> list[str]:
    """完整加载：内置模块 + （可选）entry-point 插件。

    Args:
        entry_points: 是否加载第三方 entry-point 插件。

    Returns:
        注册的 Provider 名（字典序）。
    """
    load_builtin_providers()
    if entry_points:
        provider_registry.load_entry_points()
    return provider_registry.list_names()


def get_provider_class(name: str) -> type[Any]:
    """按注册名 / kind / 别名取 Provider 类。

    Args:
        name: Provider 注册名、kind 或别名。

    Returns:
        Provider 类。

    Raises:
        ProviderError: 未注册（``LF-PROVIDER-0707``）。
    """
    load_builtin_providers()
    cls = provider_registry.get_or_default(name)
    if cls is None:
        raise ProviderError(
            f"Provider {name!r} 未注册",
            details={
                "provider": name,
                "known": provider_registry.list_names(),
                "aliases": provider_registry.list_aliases(),
            },
        )
    return cls


def create_provider(
    config: ProviderConfigModel, *, clock: Clock | None = None
) -> Any:
    """按配置实例化一个 Provider。

    Args:
        config: 内存配置。
        clock: 时间源；``None`` 时用进程级默认时钟。

    Returns:
        已实例化的 Provider。

    Raises:
        ProviderError: 配置的 ``kind`` 无法解析为已注册的 Provider 类。
    """
    cls = get_provider_class(config.kind)
    try:
        return cls(config, clock or get_clock())
    except TypeError as exc:
        # 类存在但构造签名不兼容 —— 属于实现 bug，给出可诊断信息。
        raise ProviderError(
            f"Provider 类 {cls.__name__} 构造失败",
            details={"provider": config.name, "kind": config.kind, "error": str(exc)},
        ) from exc


def create_mock_provider(
    *,
    name: str = "mock",
    model: str = "mock-large",
    options: dict[str, Any] | None = None,
    clock: Clock | None = None,
) -> Any:
    """快捷构造一个 MockProvider（零 Key 场景的默认姿势）。

    Args:
        name: Provider 名。
        model: 默认模型。
        options: 额外配置（如故障注入 ``faults``）。
        clock: 时间源。

    Returns:
        已实例化的 MockProvider。
    """
    return create_provider(
        ProviderConfigModel(
            name=name,
            kind="mock",
            default_model=model,
            options=dict(options or {}),
            enabled=True,
        ),
        clock=clock,
    )


def get_provider(
    name: str,
    config: ProviderConfigModel | None = None,
    *,
    clock: Clock | None = None,
) -> Any:
    """按名字实例化 Provider（配置缺省时用最小默认配置）。

    Args:
        name: Provider 注册名 / kind / 别名。
        config: 配置；``None`` 时生成一个只有名字的最小配置。
        clock: 时间源。

    Returns:
        已实例化的 Provider。

    Raises:
        ProviderError: 未注册。
    """
    effective = config or ProviderConfigModel(name=name, kind=name)
    return create_provider(effective, clock=clock)
