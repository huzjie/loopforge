"""记忆层注册 —— ``layer_for`` 的便捷门面。

设计遵循 ``TASK_BREAKDOWN`` §13.3：注册表式自动发现，避免公共文件
被反复修改。这里只提供查找函数，层实现各自位于独立模块。
"""

from __future__ import annotations

from loopforge.core.enums import MemoryLayer
from loopforge.memory.layers.base import BaseMemoryLayer, layer_for

__all__ = ["BaseMemoryLayer", "get_layer", "layer_for", "AVAILABLE_LAYERS"]

#: 可用记忆层清单。
AVAILABLE_LAYERS: tuple[MemoryLayer, ...] = (
    MemoryLayer.WORKING,
    MemoryLayer.EPISODIC,
    MemoryLayer.SEMANTIC,
    MemoryLayer.MILESTONE,
)


def get_layer(uow: object, layer: MemoryLayer) -> BaseMemoryLayer:
    """按枚举取层实例（同 :func:`layer_for`）。

    :param uow: 事务上下文。
    :param layer: 记忆层。
    :returns: 层实例。
    """
    return layer_for(uow, layer)  # type: ignore[arg-type]
