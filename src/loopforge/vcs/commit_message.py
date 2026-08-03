"""结构化 commit message —— ``<type>(<scope>): <subject>`` + 四个冻结 trailer。

来源：``docs/TASK_BREAKDOWN.md`` §11.11（**trailer 键名逐字冻结**）::

    <type>(<scope>): <subject>

    <body>

    LoopForge-Run: <run_id>
    LoopForge-Task: <task_id>
    LoopForge-Step-Key: <idempotency_key>
    LoopForge-Trace: <trace_id>

约定
----
* ``git_ops.probe()`` 靠扫描 ``LoopForge-Step-Key`` 判定某个 Step 是否真的已提交
  —— **键名一个字符都不能改**。
* :func:`build_commit_message` 的产物必须能被 ``git log --format=%(trailers)``
  正确解析：trailer 块与正文之间空一行，键值之间用 ``: `` 分隔。
* :func:`parse_trailers` 解析任意文本中的 trailer 块（兼容 ``%(trailers)``
  输出与 ``git log --format=%(trailers:key=LoopForge-Step-Key,valueonly)``
  两种形态）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "TRAILER_RUN",
    "TRAILER_TASK",
    "TRAILER_STEP_KEY",
    "TRAILER_TRACE",
    "ALL_TRAILER_KEYS",
    "COMMIT_TYPES",
    "CommitMessage",
    "build_commit_message",
    "parse_trailers",
    "extract_trailer",
    "trailers_block",
    "validate_commit_subject",
]

#: 冻结 trailer 键名（§11.11）。
TRAILER_RUN: Final[str] = "LoopForge-Run"
TRAILER_TASK: Final[str] = "LoopForge-Task"
TRAILER_STEP_KEY: Final[str] = "LoopForge-Step-Key"
TRAILER_TRACE: Final[str] = "LoopForge-Trace"

#: 全部冻结 trailer 键。
ALL_TRAILER_KEYS: Final[tuple[str, ...]] = (
    TRAILER_RUN,
    TRAILER_TASK,
    TRAILER_STEP_KEY,
    TRAILER_TRACE,
)

#: 允许的 commit 类型（Conventional Commits 子集）。
COMMIT_TYPES: Final[tuple[str, ...]] = (
    "feat",
    "fix",
    "refactor",
    "docs",
    "test",
    "chore",
    "perf",
    "style",
    "build",
    "ci",
)

_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z]+(?:\([a-zA-Z0-9_./-]+\))?: .+$")


@dataclass(frozen=True)
class CommitMessage:
    """一条结构化 commit message。"""

    type: str = "chore"
    scope: str = ""
    subject: str = ""
    body: str = ""
    trailers: dict[str, str] = field(default_factory=dict)

    @property
    def subject_line(self) -> str:
        """``<type>(<scope>): <subject>`` 首行。"""
        if self.scope:
            return f"{self.type}({self.scope}): {self.subject}"
        return f"{self.type}: {self.subject}"

    def render(self) -> str:
        """渲染完整 message（首行 + 空行 + body + 空行 + trailer 块）。"""
        lines = [self.subject_line]
        if self.body:
            lines.append("")
            lines.append(self.body.strip())
        trailer_text = trailers_block(self.trailers)
        if trailer_text:
            lines.append("")
            lines.append(trailer_text)
        return "\n".join(lines)


def validate_commit_subject(subject: str) -> str:
    """校验并归一 subject（``type(scope): text`` 形态）。

    Args:
        subject: 原始 subject。

    Returns:
        归一后的 subject。

    Raises:
        ValueError: 不符合 Conventional Commits 形态。
    """
    text = (subject or "").strip()
    if not _SUBJECT_RE.match(text):
        raise ValueError(
            f"commit subject 必须是 <type>(<scope>): <subject> 形态，收到 {text!r}"
        )
    return text


def trailers_block(trailers: dict[str, str]) -> str:
    """把 trailer 字典渲染成文本块（键排序，保证确定性）。"""
    if not trailers:
        return ""
    return "\n".join(f"{key}: {value}" for key, value in sorted(trailers.items()))


def build_commit_message(
    *,
    type: str = "chore",
    scope: str = "",
    subject: str,
    body: str = "",
    run_id: str = "",
    task_id: str = "",
    step_key: str = "",
    trace_id: str = "",
    extra_trailers: dict[str, str] | None = None,
) -> str:
    """构造完整 commit message（含四个冻结 trailer）。

    Args:
        type: commit 类型（``feat`` / ``fix`` / ...）。
        scope: 影响范围（可为空）。
        subject: 主题（必填）。
        body: 正文（可为空）。
        run_id: Run ID → ``LoopForge-Run``。
        task_id: Task ID → ``LoopForge-Task``。
        step_key: 幂等键 → ``LoopForge-Step-Key``。
        trace_id: 链路 ID → ``LoopForge-Trace``。
        extra_trailers: 额外 trailer（会被冻结键覆盖保护）。

    Returns:
        完整 message 文本。

    Raises:
        ValueError: subject 非法或 type 不在允许集。
    """
    type_norm = (type or "chore").strip().lower()
    if type_norm not in COMMIT_TYPES:
        raise ValueError(f"commit type {type_norm!r} 不在允许集 {COMMIT_TYPES}")
    scope_norm = (scope or "").strip()
    subject_norm = (subject or "").strip()
    if not subject_norm:
        raise ValueError("commit subject 不能为空")
    # 校验首行形态：type(scope): subject
    validate_commit_subject(f"{type_norm}({scope_norm}): {subject_norm}".replace("()", ""))
    trailers: dict[str, str] = dict(extra_trailers or {})
    # 冻结键强制覆盖（不允许 extra 污染）
    trailers[TRAILER_RUN] = run_id
    trailers[TRAILER_TASK] = task_id
    trailers[TRAILER_STEP_KEY] = step_key
    trailers[TRAILER_TRACE] = trace_id
    msg = CommitMessage(
        type=type_norm,
        scope=scope_norm,
        subject=subject_norm,
        body=(body or "").strip(),
        trailers=trailers,
    )
    return msg.render()


_TRAILER_LINE_RE: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*):\s*(.*)$")


def parse_trailers(text: str) -> dict[str, str]:
    """从文本中解析 trailer 键值对。

    兼容：
    * ``git log --format=%(trailers)`` 输出（逐行 ``Key: value``）；
    * ``git log --format=%(trailers:key=...,valueonly)`` 输出（只有值，
      无法还原键，忽略）；
    * 普通 commit message 全文（只取末尾 trailer 块）。

    Args:
        text: 待解析文本。

    Returns:
        键 → 值 字典。
    """
    result: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        match = _TRAILER_LINE_RE.match(line)
        if match:
            key, value = match.group(1), match.group(2).strip()
            result[key] = value
    return result


def extract_trailer(text: str, key: str) -> str:
    """提取单个 trailer 值（不存在返回空串）。"""
    return parse_trailers(text).get(key, "")
