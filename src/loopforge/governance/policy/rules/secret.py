"""密钥泄露检测 —— 正则匹配常见密钥格式。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``policy/rules/secret.py``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "SECRET_PATTERNS",
    "SecretMatch",
    "find_secrets",
    "has_secret",
    "SecretRule",
]


@dataclass(slots=True)
class SecretMatch:
    """一处密钥命中。"""

    pattern_name: str
    match_text: str
    start: int
    end: int

    def to_dict(self) -> dict[str, object]:
        """转字典。"""
        return {
            "pattern_name": self.pattern_name,
            "match_text": self.match_text[:32] + ("..." if len(self.match_text) > 32 else ""),
        }


#: 常见密钥正则库。
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "aws_access_key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "aws_secret_key",
        re.compile(r"(?i)aws.{0,20}['\"][0-9a-zA-Z/+]{40}['\"]"),
    ),
    (
        "gcp_api_key",
        re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    ),
    (
        "github_token",
        re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,255}\b"),
    ),
    (
        "github_oauth",
        re.compile(r"\bgithub_pat_[0-9A-Za-z_]{22,}\b"),
    ),
    (
        "slack_token",
        re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b"),
    ),
    (
        "stripe_live",
        re.compile(r"\bsk_live_(?:REDACTED_EXAMPLE_)?[0-9a-zA-Z]{16,}\b"),
    ),
    (
        "stripe_test",
        re.compile(r"\bsk_test_(?:REDACTED_EXAMPLE_)?[0-9a-zA-Z]{16,}\b"),
    ),
    (
        "private_key_block",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    (
        "google_oauth",
        re.compile(r"\bGOCSPX-[0-9A-Za-z\-_]{20,}\b"),
    ),
    (
        "openai_key",
        re.compile(r"\bsk-(?:proj-)?[0-9A-Za-z\-_]{20,}\b"),
    ),
    (
        "anthropic_key",
        re.compile(r"\bsk-ant-[0-9A-Za-z\-_]{20,}\b"),
    ),
    (
        "sendgrid_key",
        re.compile(r"\bSG\.[0-9A-Za-z\-_]{22,}\.[0-9A-Za-z\-_]{22,}\b"),
    ),
    (
        "twilio_sid",
        re.compile(r"\bAC[0-9a-f]{32}\b"),
    ),
    (
        "basic_auth_in_url",
        re.compile(r"https?://[^:/\s]+:[^@/\s]+@"),
    ),
    (
        "generic_high_entropy",
        re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[0-9a-zA-Z_\-]{16,}['\"]?"),
    ),
)


def find_secrets(content: str) -> list[SecretMatch]:
    """扫描文本中的密钥。

    :param content: 待扫描文本。
    :returns: 命中列表（按位置排序）。
    """
    if not content:
        return []
    matches: list[SecretMatch] = []
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(content):
            matches.append(
                SecretMatch(
                    pattern_name=name,
                    match_text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )
    matches.sort(key=lambda item: (item.start, item.end))
    return matches


def has_secret(content: str) -> bool:
    """是否包含任何密钥。

    :param content: 待扫描文本。
    :returns: 是否命中。
    """
    return bool(find_secrets(content))


class SecretRule:
    """密钥规则对象。"""

    def evaluate(self, content_sample: str | None) -> tuple[bool, str]:
        """判定文本是否泄露密钥。

        :param content_sample: 待扫描内容样本。
        :returns: ``(是否安全, 证据)``。
        """
        if not content_sample:
            return True, "无内容样本"
        matches = find_secrets(content_sample)
        if matches:
            names = ", ".join(sorted({item.pattern_name for item in matches}))
            return False, f"检测到密钥泄露：{names}"
        return True, "未检测到密钥"
