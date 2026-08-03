"""LoopForge —— 开源、自托管、模型无关的长程自主编程智能体编排与治理平台。

顶层包只做三件事：暴露版本号、暴露异常根类、暴露配置入口。
**业务代码请从具体子包导入**（``loopforge.core`` / ``loopforge.db`` / ``loopforge.models``），
不要把顶层 ``__init__`` 当作大杂烩出口。

Examples:
    >>> import loopforge
    >>> isinstance(loopforge.__version__, str)
    True
"""

from __future__ import annotations

from typing import Final

from loopforge.core.config import Settings, get_settings, reload_settings
from loopforge.core.errors import LoopForgeError

__all__ = [
    "__version__",
    "__author__",
    "__license__",
    "LoopForgeError",
    "Settings",
    "get_settings",
    "reload_settings",
]

#: 语义化版本号，与 ``pyproject.toml`` 的 ``project.version`` 保持一致。
__version__: Final[str] = "0.1.0"

#: 作者标识。
__author__: Final[str] = "LoopForge Contributors"

#: 开源许可证。
__license__: Final[str] = "Apache-2.0"
