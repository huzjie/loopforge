"""网络规则 —— 域名白名单 / 黑名单 / 私网地址判定。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``policy/rules/network.py``。
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

__all__ = [
    "extract_host",
    "is_private_host",
    "is_allowed_domain",
    "match_network_rule",
    "NetworkRule",
]

#: 常见内部域名后缀（无公网解析意义）。
_PRIVATE_SUFFIXES: tuple[str, ...] = (
    ".local",
    ".internal",
    ".lan",
    ".localhost",
    ".test",
    ".example",
    ".invalid",
    ".home",
)

#: 纯 IP 正则。
_IP_RE: re.Pattern[str] = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def extract_host(url: str) -> str:
    """从 URL 提取主机名（小写、去端口）。

    :param url: URL 或裸主机名。
    :returns: 主机名；解析失败返回 ``""``。
    """
    text = (url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "//" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    host = parsed.hostname or ""
    return host.lower()


def is_private_host(host: str) -> bool:
    """判断主机是否私网 / 保留地址。

    :param host: 主机名或 IP。
    :returns: 是否私网。
    """
    if not host:
        return True
    for suffix in _PRIVATE_SUFFIXES:
        if host.endswith(suffix):
            return True
    if host in ("localhost", "0.0.0.0"):
        return True
    if _IP_RE.match(host):
        try:
            return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
        except ValueError:
            return True
    return False


def is_allowed_domain(host: str, allowlist: list[str], denylist: list[str] | None = None) -> bool:
    """域名白名单判定（子域匹配父域）。

    :param host: 主机名。
    :param allowlist: 允许的域列表（如 ``github.com`` 允许 ``api.github.com``）。
    :param denylist: 拒绝的域列表。
    :returns: 是否允许。
    """
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    for deny in denylist or []:
        deny = deny.lower().rstrip(".")
        if host == deny or host.endswith("." + deny):
            return False
    if not allowlist:
        return True
    for allow in allowlist:
        allow = allow.lower().rstrip(".")
        if host == allow or host.endswith("." + allow):
            return True
    return False


def match_network_rule(
    url: str,
    *,
    allow_domains: list[str] | None = None,
    deny_domains: list[str] | None = None,
    allow_private: bool = False,
) -> tuple[bool, str]:
    """按网络规则判定。

    :param url: 目标 URL。
    :param allow_domains: 白名单域。
    :param deny_domains: 黑名单域。
    :param allow_private: 是否允许私网地址（默认禁止 SSRF）。
    :returns: ``(是否放行, 证据)``。
    """
    host = extract_host(url)
    if not host:
        return False, "URL 无法解析主机名"
    for deny in deny_domains or []:
        if host == deny.lower() or host.endswith("." + deny.lower()):
            return False, f"命中域名黑名单：{deny}"
    if not allow_private and is_private_host(host):
        return False, f"禁止访问私网/保留地址：{host}"
    if allow_domains:
        if not is_allowed_domain(host, allow_domains):
            return False, f"域名 {host} 不在白名单内"
    return True, "网络规则放行"


class NetworkRule:
    """网络规则对象。"""

    def __init__(
        self,
        *,
        allow_domains: list[str] | None = None,
        deny_domains: list[str] | None = None,
        allow_private: bool = False,
    ) -> None:
        """初始化网络规则。"""
        self.allow_domains: list[str] = allow_domains or []
        self.deny_domains: list[str] = deny_domains or []
        self.allow_private: bool = allow_private

    def evaluate(self, url: str) -> tuple[bool, str]:
        """判定 URL 是否允许访问。

        :param url: 目标 URL。
        :returns: ``(放行, 证据)``。
        """
        return match_network_rule(
            url,
            allow_domains=self.allow_domains,
            deny_domains=self.deny_domains,
            allow_private=self.allow_private,
        )
