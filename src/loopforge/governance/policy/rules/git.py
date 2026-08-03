"""Git 规则 —— 受保护分支 / 强制推送 / 危险重置。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``policy/rules/git.py``。
"""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence

__all__ = [
    "is_protected_branch",
    "is_force_push",
    "is_dangerous_git",
    "match_git_rule",
    "GitRule",
]

#: 危险 git 命令特征。
_DANGEROUS_GIT_PATTERNS: tuple[tuple[str, ...], ...] = (
    ("push", "--force"),
    ("push", "-f"),
    ("push", "--force-with-lease", "--force"),
    ("reset", "--hard"),
    ("clean", "-f"),
    ("clean", "-fd"),
    ("clean", "-fdx"),
    ("checkout", "--", "."),
    ("filter-branch",),
    ("gc", "--prune=now"),
    ("reflog", "expire", "--expire=now", "--all"),
    ("update-ref", "-d", "refs/heads/"),
    ("branch", "-D"),
    ("tag", "-d"),
)


def is_protected_branch(branch: str, protected: Sequence[str]) -> bool:
    """判断分支是否受保护（支持 glob）。

    :param branch: 分支名。
    :param protected: 受保护分支模式（如 ``main`` / ``release/*``）。
    :returns: 是否受保护。
    """
    branch = (branch or "").strip()
    if not branch:
        return False
    for pattern in protected:
        if fnmatch.fnmatchcase(branch, pattern) or fnmatch.fnmatchcase(branch, f"refs/heads/{pattern}"):
            return True
    return False


def is_force_push(argv: Sequence[str]) -> bool:
    """判断 argv 是否为强制推送。

    :param argv: git 子命令参数。
    :returns: 是否强制推送。
    """
    args = [arg.lower() for arg in argv]
    if "push" not in args:
        return False
    return "--force" in args or "-f" in args or "--force-with-lease" in args


def is_dangerous_git(argv: Sequence[str]) -> tuple[bool, str]:
    """判定 git argv 是否危险。

    :param argv: git 子命令参数（不含 ``git`` 本身）。
    :returns: ``(是否危险, 证据)``。
    """
    args = list(argv)
    if not args:
        return False, ""
    joined = " ".join(arg.lower() for arg in args)
    for pattern in _DANGEROUS_GIT_PATTERNS:
        if all(token in args or token in joined for token in pattern):
            if pattern[0] == "push" and not is_force_push(args):
                continue
            return True, f"命中危险 git 模式：{' '.join(pattern)}"
    return False, ""


def match_git_rule(
    *,
    branch: str | None = None,
    argv: Sequence[str] | None = None,
    protected_branches: Sequence[str] | None = None,
) -> tuple[bool, str]:
    """按 git 规则判定。

    :param branch: 目标分支。
    :param argv: git 子命令参数。
    :param protected_branches: 受保护分支模式。
    :returns: ``(是否放行, 证据)``。
    """
    if argv:
        dangerous, evidence = is_dangerous_git(argv)
        if dangerous:
            return False, evidence
    if branch and protected_branches:
        if is_protected_branch(branch, protected_branches):
            return False, f"分支 {branch} 受保护，禁止直接写入"
    return True, "git 规则放行"


class GitRule:
    """Git 规则对象。"""

    def __init__(self, *, protected_branches: Sequence[str] | None = None) -> None:
        """初始化 Git 规则。"""
        self.protected_branches: list[str] = list(protected_branches or [])

    def evaluate(
        self, *, branch: str | None = None, argv: Sequence[str] | None = None
    ) -> tuple[bool, str]:
        """判定 git 操作是否允许。

        :param branch: 目标分支。
        :param argv: git 子命令参数。
        :returns: ``(放行, 证据)``。
        """
        return match_git_rule(
            branch=branch, argv=argv, protected_branches=self.protected_branches
        )
