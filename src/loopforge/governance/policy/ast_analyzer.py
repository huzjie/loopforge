"""命令 AST 分析器 —— 拆词 + 混淆识别（G2 的「AST 层」）。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``policy/ast_analyzer.py``。

:class:`AstAnalyzer` 用 :mod:`shlex` 把命令行拆成词元树，识别：

* **命令替换**：``$(...)`` / 反引号；
* **管道链**：``a | b``，特别是 ``curl | sh`` / ``wget | bash``；
* **解码执行**：base64 / hex / 双层编码；
* **重定向到系统文件**：``> /etc/...`` / ``> /dev/sda``；
* **嵌套 shell**：``sh -c`` / ``bash -c`` / ``python -c``。

产出 :class:`AstReport`（一组布尔旗标 + 证据）。
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field

from loopforge.governance.policy.rules.command import decode_obfuscation, shell_words

__all__ = ["AstAnalyzer", "AstReport"]


@dataclass(slots=True)
class AstReport:
    """命令 AST 分析报告。"""

    command: str = ""
    has_command_substitution: bool = False
    has_backtick: bool = False
    has_pipe_to_shell: bool = False
    has_nested_shell: bool = False
    has_decode_exec: bool = False
    has_redirect_to_system: bool = False
    has_heredoc: bool = False
    dangerous: bool = False
    evidence: list[str] = field(default_factory=list)

    @property
    def flags(self) -> dict[str, bool]:
        """全部旗标（供策略规则引用）。"""
        return {
            "command_substitution": self.has_command_substitution,
            "backtick": self.has_backtick,
            "pipe_to_shell": self.has_pipe_to_shell,
            "nested_shell": self.has_nested_shell,
            "decode_exec": self.has_decode_exec,
            "redirect_to_system": self.has_redirect_to_system,
            "heredoc": self.has_heredoc,
        }

    def to_dict(self) -> dict[str, object]:
        """转字典。"""
        return {
            "command": self.command[:200],
            **self.flags,
            "dangerous": self.dangerous,
            "evidence": list(self.evidence),
        }


#: 管道到解释器的特征。
_PIPE_SHELL_RE: re.Pattern[str] = re.compile(
    r"\|\s*(?:sudo\s+)?(?:sh|bash|zsh|dash|ksh|python|python3|perl|ruby)\b",
    re.IGNORECASE,
)
#: 命令替换。
_SUBSTITUTION_RE: re.Pattern[str] = re.compile(r"\$\(|`")
#: 嵌套解释器。
_NESTED_SHELL_RE: re.Pattern[str] = re.compile(
    r"\b(?:sh|bash|zsh|dash|python|python3|perl|ruby)\s+-c\b",
    re.IGNORECASE,
)
#: 重定向到系统关键路径。
_REDIRECT_SYSTEM_RE: re.Pattern[str] = re.compile(
    r"(?:^|[;&|]\s*)[^|]*>\s*/?(?:etc/(?:passwd|shadow|sudoers)|dev/(?:sda|sdb|mem|kmem)|proc/(?:kcore|sys))",
    re.IGNORECASE,
)
#: heredoc。
_HEREDOC_RE: re.Pattern[str] = re.compile(r"<<\s*-?\w+")


class AstAnalyzer:
    """命令 AST 分析器。"""

    def analyze(self, command: str | Sequence[str]) -> AstReport:
        """分析命令行。

        :param command: 命令行字符串或 argv。
        :returns: :class:`AstReport`。
        """
        text = command if isinstance(command, str) else " ".join(command)
        report = AstReport(command=text)
        if not text:
            return report

        if _SUBSTITUTION_RE.search(text):
            report.has_command_substitution = True
            report.evidence.append("检测到命令替换 $() 或反引号")
        if "`" in text:
            report.has_backtick = True
        if _PIPE_SHELL_RE.search(text):
            report.has_pipe_to_shell = True
            report.evidence.append("检测到管道到解释器（curl|sh 类）")
        if _NESTED_SHELL_RE.search(text):
            report.has_nested_shell = True
            report.evidence.append("检测到嵌套解释器 -c")
        if _REDIRECT_SYSTEM_RE.search(text):
            report.has_redirect_to_system = True
            report.evidence.append("检测到重定向到系统关键文件")
        if _HEREDOC_RE.search(text):
            report.has_heredoc = True

        words = shell_words(command)
        decoded = decode_obfuscation(words)
        if decoded:
            report.has_decode_exec = True
            report.evidence.append("检测到 base64/hex 解码负载")

        report.dangerous = (
            report.has_pipe_to_shell
            or report.has_decode_exec
            or report.has_redirect_to_system
            or (report.has_command_substitution and report.has_nested_shell)
        )
        return report
