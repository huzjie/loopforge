"""规则实现包 —— 各分类规则对象与分类分派。

分类 → 判定函数映射：

* ``shell``      → :mod:`command`（正则 + AST 双重判定）
* ``path``       → :mod:`path`（路径沙箱）
* ``network``    → :mod:`network`（域名白名单 / SSRF 防护）
* ``git``        → :mod:`git`（受保护分支 / 强制推送）
* ``secret``     → :mod:`secret`（密钥泄露检测）
* ``dependency`` → :mod:`dependency`（高危依赖）
"""

from __future__ import annotations

from loopforge.governance.policy.rules.command import is_dangerous_shell
from loopforge.governance.policy.rules.dependency import DependencyRule
from loopforge.governance.policy.rules.git import GitRule
from loopforge.governance.policy.rules.network import NetworkRule
from loopforge.governance.policy.rules.path import PathRule
from loopforge.governance.policy.rules.secret import SecretRule

__all__ = [
    "DependencyRule",
    "GitRule",
    "NetworkRule",
    "PathRule",
    "SecretRule",
    "is_dangerous_shell",
]
