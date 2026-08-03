"""依赖规则 —— 高风险依赖名 / 版本判定。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``policy/rules/dependency.py``。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

__all__ = [
    "KNOWN_RISKY_PACKAGES",
    "is_risky_package",
    "is_risky_version",
    "match_dependency_rule",
    "DependencyRule",
]

#: 已知高危 / 恶意包名（含 typosquatting 常见变体）。
KNOWN_RISKY_PACKAGES: frozenset[str] = frozenset(
    {
        "pytorch", "torch", "tensorflow", "numpy", "scikit-learn", "pandas",
        "matplotlib", "requests", "urllib3", "pip", "setuptools", "wheel",
        "babel", "certifi", "cffi", "charset-normalizer", "click", "cryptography",
        "idna", "jinja2", "markupsafe", "packaging", "pillow", "pyyaml", "pyyaml",
        "regex", "six", "typing-extensions", "tzdata", "werkzeug", "flask",
        "django", "fastapi", "pydantic", "sqlalchemy", "asyncpg", "psycopg2",
        "redis", "celery", "gunicorn", "uvicorn",
    }
)

#: 危险版本特征（<0.1 / 1.0.0 前夜版 / 预发布等）。
_RISKY_VERSION_RE: re.Pattern[str] = re.compile(
    r"^(0\.0\.|0\.1\.|0\.2\.|0\.9\.|1\.0\.0[ab]|2\.0\.0[ab])|[-+]dev|[-+]alpha|[-+]beta|[-+]rc\d*$",
    re.IGNORECASE,
)


def is_risky_package(name: str) -> bool:
    """判断包名是否高危（大小写不敏感）。

    :param name: 包名。
    :returns: 是否高危。
    """
    return (name or "").strip().lower() in KNOWN_RISKY_PACKAGES


def is_risky_version(version: str | None) -> bool:
    """判断版本是否危险（过旧 / 预发布）。

    :param version: 版本号；``None`` 或空视为不危险（无法判断）。
    :returns: 是否危险。
    """
    if not version:
        return False
    return _RISKY_VERSION_RE.search(version.strip()) is not None


def match_dependency_rule(
    *,
    name: str | None = None,
    version: str | None = None,
    deny_names: list[str] | None = None,
    deny_version_patterns: list[str] | None = None,
) -> tuple[bool, str]:
    """按依赖规则判定。

    :param name: 依赖名。
    :param version: 依赖版本。
    :param deny_names: 额外拒绝的包名。
    :param deny_version_patterns: 额外拒绝的版本正则。
    :returns: ``(是否放行, 证据)``。
    """
    if name:
        normalized = name.strip().lower()
        if is_risky_package(normalized):
            return False, f"高危/知名包名（防 typosquatting）：{name}"
        for deny in deny_names or []:
            if normalized == deny.strip().lower():
                return False, f"命中依赖黑名单：{deny}"
    if version:
        if is_risky_version(version):
            return False, f"危险版本：{version}"
        for pattern in deny_version_patterns or []:
            if re.search(pattern, version, re.IGNORECASE):
                return False, f"命中版本黑名单正则：{pattern}"
    return True, "依赖规则放行"


class DependencyRule:
    """依赖规则对象。"""

    def __init__(
        self,
        *,
        deny_names: list[str] | None = None,
        deny_version_patterns: list[str] | None = None,
    ) -> None:
        """初始化依赖规则。"""
        self.deny_names: list[str] = deny_names or []
        self.deny_version_patterns: list[str] = deny_version_patterns or []

    def evaluate(self, context: Mapping[str, object]) -> tuple[bool, str]:
        """判定依赖操作是否允许。

        :param context: 含 ``name`` / ``version`` 的上下文。
        :returns: ``(放行, 证据)``。
        """
        return match_dependency_rule(
            name=str(context.get("name") or "") if context.get("name") else None,
            version=str(context.get("version") or "") if context.get("version") else None,
            deny_names=self.deny_names,
            deny_version_patterns=self.deny_version_patterns,
        )
