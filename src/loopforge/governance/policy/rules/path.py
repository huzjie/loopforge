"""路径规则 —— 路径沙箱（允许/拒绝 glob）。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``policy/rules/path.py``。
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

__all__ = [
    "match_glob",
    "is_within",
    "match_path_rule",
    "PathRule",
]

#: 危险路径特征（正则）。
DANGEROUS_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/etc/(passwd|shadow|sudoers|ld\.so\.preload)$", re.IGNORECASE),
    re.compile(r"/dev/(sda|sdb|sdc|mem|kmem|nvme)", re.IGNORECASE),
    re.compile(r"/proc/(kcore|sys)", re.IGNORECASE),
    re.compile(r"/boot/", re.IGNORECASE),
    re.compile(r"^\.\.(\\|/|$)", re.IGNORECASE),
    re.compile(r"\.\.(\\|/)\.\.", re.IGNORECASE),
    re.compile(r"/\.git/(config|objects)/", re.IGNORECASE),
)


def match_glob(path: str, pattern: str) -> bool:
    """glob 匹配（兼容 Windows 分隔符）。

    ``**/`` 前缀同时匹配「相对路径」（``**/src/**`` 命中 ``src/util.py``）。

    :param path: 待匹配路径。
    :param pattern: glob 模式（如 ``src/**``）。
    :returns: 是否匹配。
    """
    normalized_path = str(Path(path).as_posix()).lower()
    normalized_pattern = str(Path(pattern).as_posix()).lower()
    if fnmatch.fnmatchcase(normalized_path, normalized_pattern):
        return True
    # ``**/`` 前缀：允许匹配不带前导目录的相对路径。
    if normalized_pattern.startswith("**/"):
        stripped = normalized_pattern[3:]
        if fnmatch.fnmatchcase(normalized_path, stripped):
            return True
        if fnmatch.fnmatchcase(normalized_path, "**/" + stripped):
            return True
    return False


def is_within(path: str, root: str) -> bool:
    """判断路径是否在沙箱根目录内（防目录穿越）。

    :param path: 目标路径。
    :param root: 沙箱根。
    :returns: 是否在根内。
    """
    target = Path(path).resolve()
    base = Path(root).resolve()
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


def match_path_rule(
    path: str,
    *,
    deny_globs: list[str] | None = None,
    allow_globs: list[str] | None = None,
    sandbox_root: str | None = None,
) -> tuple[bool, str]:
    """按路径规则判定。

    :param path: 目标路径。
    :param deny_globs: 拒绝 glob 列表。
    :param allow_globs: 允许 glob 列表（非沙箱路径必须匹配才放行）。
    :param sandbox_root: 沙箱根；给定则强制路径必须在根内。
    :returns: ``(是否放行, 证据)``。
    """
    if not path:
        return False, "路径为空"
    for pattern in DANGEROUS_PATH_PATTERNS:
        if pattern.search(str(Path(path).as_posix())):
            return False, f"命中危险路径特征：{pattern.pattern}"
    for deny in deny_globs or []:
        if match_glob(path, deny):
            return False, f"命中拒绝 glob：{deny}"
    if sandbox_root is not None and not is_within(path, sandbox_root):
        return False, f"路径 {path} 超出沙箱根 {sandbox_root}"
    if allow_globs:
        if any(match_glob(path, allow) for allow in allow_globs):
            return True, "命中允许 glob"
        return False, f"路径 {path} 未命中任何允许 glob"
    return True, "路径规则放行"


class PathRule:
    """路径规则对象（供策略引擎调用）。"""

    def __init__(
        self,
        *,
        deny_globs: list[str] | None = None,
        allow_globs: list[str] | None = None,
        sandbox_root: str | None = None,
    ) -> None:
        """初始化路径规则。"""
        self.deny_globs: list[str] = deny_globs or []
        self.allow_globs: list[str] = allow_globs or []
        self.sandbox_root: str | None = sandbox_root

    def evaluate(self, path: str) -> tuple[bool, str]:
        """判定路径是否允许。

        :param path: 目标路径。
        :returns: ``(放行, 证据)``。
        """
        return match_path_rule(
            path,
            deny_globs=self.deny_globs,
            allow_globs=self.allow_globs,
            sandbox_root=self.sandbox_root,
        )
