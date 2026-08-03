"""失败分类器 —— 把原始输出归类成 :class:`FailureClass`。

§5.3.6：**verification 的失败必须结构化**，failure_router 输出的失败分类是
WP4 生成修复子任务的输入，不能只给一段 stderr 文本。
"""

from __future__ import annotations

import re
from typing import Any

from loopforge.verification.base import FailureClass

__all__ = ["classify_failure", "extract_failure_hints"]

_COMPILE_PATTERNS = (
    re.compile(r"\b(?:error|错误):", re.IGNORECASE),
    re.compile(r"\bSyntaxError\b", re.IGNORECASE),
    re.compile(r"\bModuleNotFoundError\b", re.IGNORECASE),
    re.compile(r"\bImportError\b", re.IGNORECASE),
    re.compile(r"\bIndentationError\b", re.IGNORECASE),
    re.compile(r"\bNameError\b", re.IGNORECASE),
    re.compile(r"\bTypeError\b", re.IGNORECASE),
    re.compile(r"\btsc\b.*\berror TS\d+\b", re.IGNORECASE),
    re.compile(r"Failed to compile", re.IGNORECASE),
)

_ASSERT_PATTERNS = (
    re.compile(r"\bAssertionError\b", re.IGNORECASE),
    re.compile(r"\bassert\b", re.IGNORECASE),
    re.compile(r"\bexpected\b.*\bto\b.*\b(?:equal|be|contain)\b", re.IGNORECASE),
    re.compile(r"\bFAILED\b", re.IGNORECASE),
    re.compile(r"\b✗\b|\b✘\b"),
    re.compile(r"\bfailed\s+\d+\b", re.IGNORECASE),
)

_DEPENDENCY_PATTERNS = (
    re.compile(r"\bNo module named\b", re.IGNORECASE),
    re.compile(r"\bcould not (?:resolve|find)\b", re.IGNORECASE),
    re.compile(r"\bversion conflict\b", re.IGNORECASE),
    re.compile(r"\bpackage\b.*\bnot found\b", re.IGNORECASE),
    re.compile(r"\bCannot find module\b", re.IGNORECASE),
    re.compile(r"\bE404\b|\b404 Not Found\b"),
)

_ENV_PATTERNS = (
    re.compile(r"\bcommand not found\b", re.IGNORECASE),
    re.compile(r"\bnot recognized as an internal or external command\b", re.IGNORECASE),
    re.compile(r"\bPermission denied\b", re.IGNORECASE),
    re.compile(r"\bNo such file or directory\b", re.IGNORECASE),
    re.compile(r"\bEACCES\b"),
    re.compile(r"\bdisk quota\b", re.IGNORECASE),
)


def classify_failure(
    *,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
) -> FailureClass:
    """把执行结果归类为失败类型。

    Args:
        exit_code: 退出码。
        stdout: stdout。
        stderr: stderr。
        timed_out: 是否超时。

    Returns:
        :class:`FailureClass`。
    """
    if timed_out:
        return FailureClass.TIMEOUT
    if exit_code in (137, -9) or "MemoryError" in stderr or "Killed" in stderr:
        return FailureClass.TIMEOUT if exit_code == -9 else FailureClass.ENV

    combined = f"{stdout}\n{stderr}"

    if any(pattern.search(combined) for pattern in _DEPENDENCY_PATTERNS):
        return FailureClass.DEPENDENCY
    if any(pattern.search(combined) for pattern in _COMPILE_PATTERNS):
        return FailureClass.COMPILE
    if any(pattern.search(combined) for pattern in _ASSERT_PATTERNS):
        return FailureClass.ASSERT
    if any(pattern.search(combined) for pattern in _ENV_PATTERNS):
        return FailureClass.ENV
    if exit_code != 0:
        return FailureClass.UNKNOWN
    return FailureClass.UNKNOWN


def extract_failure_hints(*, stdout: str, stderr: str, limit: int = 6) -> list[str]:
    """从输出中抽取失败线索（供修复子任务参考）。

    Args:
        stdout: stdout。
        stderr: stderr。
        limit: 最多返回的线索数。

    Returns:
        线索行列表（去重、截断）。
    """
    combined = f"{stderr}\n{stdout}"
    hints: list[str] = []
    seen: set[str] = set()
    for line in combined.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.search(
            r"(error|Error|FAILED|AssertionError|Exception|Traceback|✗|cannot|not found|No module)",
            stripped,
        ):
            key = stripped[:80]
            if key in seen:
                continue
            seen.add(key)
            hints.append(stripped[:300])
            if len(hints) >= limit:
                break
    return hints
