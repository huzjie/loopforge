"""敏感信息脱敏 —— 内置 **24 条** Secret 正则。

用途：
    * structlog 处理器（``core/logging.py``）在落盘前统一脱敏；
    * ``llm_calls.request`` / ``tool_calls.args`` 入库前脱敏；
    * API 错误响应的 ``details`` 出站前脱敏。

设计取舍：**宁可误伤，不可漏网**。正则命中即整体替换为 ``[REDACTED:<name>]``，
保留少量前缀字符以便人工排查（可通过 ``keep_prefix`` 关闭）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "SecretPattern",
    "SECRET_PATTERNS",
    "SENSITIVE_KEYS",
    "REDACTED",
    "redact_text",
    "redact_mapping",
    "redact_value",
    "mask_secret",
    "has_secret",
    "detect_secrets",
]

#: 统一替换标记。
REDACTED: Final[str] = "[REDACTED]"

#: 递归脱敏的最大深度，防御深层嵌套导致的栈溢出。
_MAX_DEPTH: Final[int] = 12


@dataclass(frozen=True, slots=True)
class SecretPattern:
    """一条 Secret 检测规则。

    Attributes:
        name: 规则名，出现在替换标记里，便于定位泄漏源。
        pattern: 已编译正则。
        group: 需要被替换的捕获组序号；``0`` 表示整体匹配。
    """

    name: str
    pattern: re.Pattern[str]
    group: int = 0


def _p(name: str, regex: str, *, group: int = 0, flags: int = 0) -> SecretPattern:
    """构造 :class:`SecretPattern` 的内部快捷函数。"""
    return SecretPattern(name=name, pattern=re.compile(regex, flags), group=group)


# ---------------------------------------------------------------------------
# 24 条 Secret 正则（顺序即匹配优先级：越具体的越靠前）
# ---------------------------------------------------------------------------
SECRET_PATTERNS: Final[tuple[SecretPattern, ...]] = (
    # 1. PEM 私钥块（多行，必须最先匹配，否则会被后面的规则切碎）
    _p(
        "private_key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{0,8192}?-----END [A-Z ]*PRIVATE KEY-----",
    ),
    # 2. OpenAI 项目密钥
    _p("openai_project_key", r"\bsk-proj-[A-Za-z0-9_\-]{20,}"),
    # 3. Anthropic 密钥
    _p("anthropic_key", r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    # 4. OpenAI / DashScope / DeepSeek 经典 sk- 密钥
    _p("openai_key", r"\bsk-[A-Za-z0-9]{20,}"),
    # 5. GitHub 个人访问令牌（经典）
    _p("github_pat", r"\bghp_[A-Za-z0-9]{36,}"),
    # 6. GitHub 细粒度令牌
    _p("github_pat_fine", r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    # 7. GitHub OAuth / App / Server / Refresh 令牌
    _p("github_oauth", r"\b(?:gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}"),
    # 8. GitLab 个人访问令牌
    _p("gitlab_pat", r"\bglpat-[A-Za-z0-9_\-]{20,}"),
    # 9. AWS Access Key ID
    _p("aws_access_key_id", r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA|AGPA|AIDA|AIPA|ANPA|ANVA|AROA)[A-Z0-9]{16}\b"),
    # 10. AWS Secret Access Key（依赖上下文关键词，避免误伤 base64 数据）
    _p(
        "aws_secret_access_key",
        r"(?:aws)?_?secret_?(?:access_?)?key\W{0,4}([A-Za-z0-9/+=]{40})",
        group=1,
        flags=re.IGNORECASE,
    ),
    # 11. Google API Key
    _p("google_api_key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    # 12. GCP 服务账号私钥 ID
    _p("gcp_private_key_id", r'"private_key_id"\s*:\s*"([0-9a-f]{40})"', group=1),
    # 13. Azure client secret
    _p(
        "azure_client_secret",
        r"azure[_\-]?client[_\-]?secret\W{0,4}([A-Za-z0-9~._\-]{20,})",
        group=1,
        flags=re.IGNORECASE,
    ),
    # 14. Slack 令牌
    _p("slack_token", r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    # 15. Slack Webhook
    _p("slack_webhook", r"https://hooks\.slack\.com/services/[A-Za-z0-9/_\-]{20,}"),
    # 16. Stripe 密钥（支持 REDACTED_EXAMPLE_ 占位样例，避免被 GitHub secret scanning 误报）
    _p("stripe_key", r"\b(?:sk|rk)_(?:live|test)_(?:REDACTED_EXAMPLE_)?[0-9a-zA-Z]{16,}"),
    # 17. SendGrid 密钥
    _p("sendgrid_key", r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"),
    # 18. npm 令牌
    _p("npm_token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    # 19. PyPI 令牌
    _p("pypi_token", r"\bpypi-[A-Za-z0-9_\-]{32,}"),
    # 20. HuggingFace 令牌
    _p("huggingface_token", r"\bhf_[A-Za-z0-9]{30,}"),
    # 21. Telegram Bot 令牌
    _p("telegram_bot_token", r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"),
    # 22. JWT（三段式 base64url）
    _p("jwt", r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    # 23. URL 内嵌基本认证凭据（含数据库连接串）
    _p(
        "url_basic_auth",
        r"\b((?:https?|postgres(?:ql)?|mysql|redis|rediss|mongodb(?:\+srv)?|amqp)"
        r"(?:\+[a-z0-9]+)?://[^\s:/@]+:)([^\s:/@]+)(@)",
        group=2,
        flags=re.IGNORECASE,
    ),
    # 24. 通用「键=值」赋值（兜底，最后匹配）
    _p(
        "generic_secret_assignment",
        r"\b(?:api[_\-]?key|apikey|secret|token|password|passwd|pwd|access[_\-]?key"
        r"|client[_\-]?secret|encryption[_\-]?key|auth(?:orization)?|bearer)\b"
        r"\s*[:=]\s*[\"']?([^\s\"',;}]{6,})",
        group=1,
        flags=re.IGNORECASE,
    ),
)

#: 映射键名命中即整值脱敏（不看值长什么样）。
SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "access_key",
        "access_token",
        "api_key",
        "api_key_enc",
        "apikey",
        "authorization",
        "bootstrap_key",
        "client_secret",
        "credentials",
        "credentials_ref",
        "encryption_key",
        "hashed_password",
        "hashed_secret",
        "password",
        "passwd",
        "private_key",
        "pwd",
        "refresh_token",
        "secret",
        "secret_key",
        "session_key",
        "signature",
        "token",
        "webhook_secret",
        "webhook_secret_enc",
    }
)


def mask_secret(value: str, *, keep_prefix: int = 4, keep_suffix: int = 0) -> str:
    """把一段密文替换成掩码，保留可辨识的少量首尾字符。

    Args:
        value: 原始密文。
        keep_prefix: 保留的前缀字符数。
        keep_suffix: 保留的后缀字符数。

    Returns:
        掩码后的字符串。短于阈值时整体替换为 :data:`REDACTED`。
    """
    if keep_prefix < 0 or keep_suffix < 0:
        raise ValueError("keep_prefix / keep_suffix 不能为负")
    if len(value) <= keep_prefix + keep_suffix + 2:
        return REDACTED
    head = value[:keep_prefix]
    tail = value[len(value) - keep_suffix :] if keep_suffix else ""
    return f"{head}***{tail}"


def redact_text(text: str, *, keep_prefix: int = 4) -> str:
    """对一段自由文本执行 24 条规则的脱敏。

    Args:
        text: 原始文本（日志行、prompt、命令行、堆栈等）。
        keep_prefix: 每个命中片段保留的前缀长度，便于人工定位；传 ``0`` 则全遮蔽。

    Returns:
        脱敏后的文本；未命中任何规则时原样返回。

    Examples:
        >>> redact_text("export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx")
        'export OPENAI_API_KEY=sk-a***'
    """
    if not text:
        return text
    result = text
    for rule in SECRET_PATTERNS:

        def _replace(match: re.Match[str], _rule: SecretPattern = rule) -> str:
            target = match.group(_rule.group)
            if not target:
                return match.group(0)
            masked = mask_secret(target, keep_prefix=keep_prefix)
            whole = match.group(0)
            if _rule.group == 0:
                return masked
            # 只替换捕获组，保留上下文（如 "password=" 前缀）。
            start = match.start(_rule.group) - match.start(0)
            end = match.end(_rule.group) - match.start(0)
            return whole[:start] + masked + whole[end:]

        result = rule.pattern.sub(_replace, result)
    return result


def redact_value(value: Any, *, key: str | None = None, _depth: int = 0) -> Any:
    """递归脱敏任意值（字符串 / 映射 / 序列 / 标量）。

    Args:
        value: 待脱敏值。
        key: 该值在父映射中的键名；命中 :data:`SENSITIVE_KEYS` 时整值遮蔽。
        _depth: 内部递归深度计数，调用方无需传。

    Returns:
        脱敏后的同构值。深度超限时返回 ``"[TRUNCATED]"``。
    """
    if _depth > _MAX_DEPTH:
        return "[TRUNCATED]"
    if key is not None and key.strip().lower() in SENSITIVE_KEYS:
        return REDACTED if value is not None else None
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(child_key): redact_value(child_value, key=str(child_key), _depth=_depth + 1)
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [redact_value(item, _depth=_depth + 1) for item in value]
        if isinstance(value, tuple):
            return tuple(items)
        if isinstance(value, (set, frozenset)):
            # set 内元素脱敏后可能重复，转 list 保证可 JSON 化。
            return items
        return items
    if isinstance(value, (bytes, bytearray)):
        return REDACTED
    return value


def redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """对映射执行递归脱敏（structlog event_dict / LLM 请求体入口）。

    Args:
        mapping: 原始映射。

    Returns:
        全新的脱敏字典，**不修改入参**。
    """
    return {
        str(key): redact_value(value, key=str(key), _depth=1) for key, value in mapping.items()
    }


def detect_secrets(text: str) -> list[str]:
    """列出文本命中的规则名（用于测试与安全审计）。

    Args:
        text: 待检测文本。

    Returns:
        去重后的规则名列表，保持规则声明顺序。
    """
    hits: list[str] = []
    for rule in SECRET_PATTERNS:
        if rule.pattern.search(text) and rule.name not in hits:
            hits.append(rule.name)
    return hits


def has_secret(text: str) -> bool:
    """判断文本是否含有疑似密钥。

    Args:
        text: 待检测文本。

    Returns:
        命中任意一条规则返回 ``True``。
    """
    return any(rule.pattern.search(text) for rule in SECRET_PATTERNS)


def _sanity_check() -> None:
    """模块自检：确认正好 24 条规则（CI 与单测都会断言）。"""
    if len(SECRET_PATTERNS) != 24:
        raise AssertionError(f"SECRET_PATTERNS 必须是 24 条，当前 {len(SECRET_PATTERNS)}")
    names = [rule.name for rule in SECRET_PATTERNS]
    if len(set(names)) != len(names):
        raise AssertionError("SECRET_PATTERNS 规则名重复")


_sanity_check()


def redact_sequence(values: Sequence[Any]) -> list[Any]:
    """对序列执行脱敏（命令行 argv 等场景）。

    Args:
        values: 原始序列。

    Returns:
        脱敏后的新列表。
    """
    return [redact_value(item, _depth=1) for item in values]
