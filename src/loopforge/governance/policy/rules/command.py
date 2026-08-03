"""危险命令规则 —— 正则 + AST 双重判定（G2）。

来源：``docs/TASK_BREAKDOWN.md`` §7.3.2（G2：只做正则一定会被
``$(echo cm0K | base64 -d)`` 这类绕过）。

:func:`is_dangerous_shell` 分两步：

1. **正则层**：对拼接后的命令行匹配危险模式库（删除、格式化、提权、
   反弹 shell、挖矿、自毁等）。
2. **AST 层**：用 :mod:`shlex` 拆词后做解码检测 —— base64 / hex / 混淆
   命令替换、管道到解释器（``curl | sh``）、重定向到系统文件等。
"""

from __future__ import annotations

import base64
import re
import shlex
from collections.abc import Sequence

__all__ = [
    "DANGEROUS_PATTERNS",
    "DANGEROUS_BINARIES",
    "DANGEROUS_TARGETS",
    "is_dangerous_shell",
    "match_dangerous_pattern",
    "decode_obfuscation",
    "shell_words",
]

#: 危险命令模式库（大小写不敏感）。
DANGEROUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+/?\s*(\*|/|\.|\.\.)", re.IGNORECASE),
    re.compile(r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+/\s*\*", re.IGNORECASE),
    re.compile(r"\bmkfs(?:\.[a-z0-9]+)?\b", re.IGNORECASE),
    re.compile(r"\bmkfs\s", re.IGNORECASE),
    re.compile(r"\bdd\s+if=.*of=/dev/(sda|sdb|sdc|nvme|mmc)", re.IGNORECASE),
    re.compile(r"\bdd\s+of=/dev/(sda|sdb|sdc|nvme|mmc)", re.IGNORECASE),
    re.compile(r"\bfdisk\s+/dev/(sda|sdb|sdc)", re.IGNORECASE),
    re.compile(r"\bparted\s+/dev/(sda|sdb|sdc)", re.IGNORECASE),
    re.compile(r"\bcryptsetup\s+(luksFormat|luksOpen|erase|reencrypt)", re.IGNORECASE),
    re.compile(r"\bchmod\s+-R\s+777\s+/\s*$", re.IGNORECASE),
    re.compile(r"\bchmod\s+777\s+/(etc|usr|bin|boot|var|root)\b", re.IGNORECASE),
    re.compile(r"\bchown\s+-R\s+[^\s]+\s+/\s*$", re.IGNORECASE),
    re.compile(r"\bshutdown\b|\breboot\b|\bpoweroff\b|\bhalt\b|\binit\s+0\b|\btelinit\s+0\b", re.IGNORECASE),
    re.compile(r"\bkill\s+-9\s+1\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};?\s*:", re.IGNORECASE),
    re.compile(r"\b(?:python|python3|perl|ruby)\s+-c\s+.*(?:socket|subprocess|os\.system|exec\()", re.IGNORECASE),
    re.compile(r"\b(?:python|python3)\s+-c\s+.*base64", re.IGNORECASE),
    re.compile(r"\bopenssl\s+enc\s+.*-in\s+/etc/shadow", re.IGNORECASE),
    re.compile(r"\b(cat|strings|tail|head|less|more|grep)\s+/etc/shadow\b", re.IGNORECASE),
    re.compile(r"\b(cp|mv|scp|rsync)\s+.*\s+/etc/shadow\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+[0-7]{3,4}\s+/etc/shadow\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+.*\b777\s+/(etc|usr|bin|boot|var|root|home)\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+-R\s+777\s+/\s*$", re.IGNORECASE),
    re.compile(r"\bchown\s+-R\s+[^\s]+\s+/(etc|usr|bin|boot|var|root|home)\b", re.IGNORECASE),
    re.compile(r"\bchown\s+-R\s+[^\s]+\s+/\s*$", re.IGNORECASE),
    re.compile(r"\bchown\s+[^\s]+\s+/etc/passwd\b", re.IGNORECASE),
    re.compile(r"\bpasswd\s+root\b", re.IGNORECASE),
    re.compile(r"\buseradd\b|\buserdel\b|\busermod\b|\bgroupadd\b|\bgroupdel\b", re.IGNORECASE),
    re.compile(r"\bsudo\s+rm\b", re.IGNORECASE),
    re.compile(r"\bsudo\s+(mkfs|fdisk|dd|shutdown|reboot|kill)", re.IGNORECASE),
    re.compile(r"\biptables\s+-F\b|\bnft\s+flush\b|\bufw\s+disable\b", re.IGNORECASE),
    re.compile(r"\bmodprobe\b|\binsmod\b|\brmmod\b", re.IGNORECASE),
    re.compile(r"\b(?:nc|ncat|netcat)\s+.*(-e|--exec|-c)\s", re.IGNORECASE),
    re.compile(r"\bbash\s+-i\s+>&\s*/dev/tcp/", re.IGNORECASE),
    re.compile(r"\bbash\s+/dev/tcp/", re.IGNORECASE),
    re.compile(r"\b(?:bash|sh|zsh)\s*<\s*\(", re.IGNORECASE),
    re.compile(r"\btelnet\s+\S+\s+\d+", re.IGNORECASE),
    re.compile(r"\b(?:xxd|od)\s+-r\s+.*\|\s*(?:sh|bash)\b", re.IGNORECASE),
    re.compile(r"\bcurl\s+.*\|\s*(sh|bash|zsh|dash)\b", re.IGNORECASE),
    re.compile(r"\bwget\s+.*\|\s*(sh|bash|zsh|dash)\b", re.IGNORECASE),
    re.compile(r"\b(curl|wget)\s+.*-o\s+/etc/", re.IGNORECASE),
    re.compile(r"\b(curl|wget)\s+.*-o\s+/usr/(bin|local)", re.IGNORECASE),
    re.compile(r"\b(curl|wget)\s+-O\s+\S+\s+\S+.*&&", re.IGNORECASE),
    re.compile(r"\beval\s*\(?\s*[\"']?\$?\(", re.IGNORECASE),
    re.compile(r"\becho\s+[A-Za-z0-9+/=]{8,}\s*\|\s*base64\s+-d", re.IGNORECASE),
    re.compile(r"\bbase64\s+-d\s*(<<<|<<)", re.IGNORECASE),
    re.compile(r"\|\s*base64\s+(-d|--decode)\s*\|", re.IGNORECASE),
    re.compile(r"\bprintf\s+['\"][0-9a-fA-F]{8,}['\"]\s*\|\s*(xxd|base64|sh)\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+\+x\s+.*\s*&&\s*\.?/", re.IGNORECASE),
    re.compile(r"\btee\s+/etc/(passwd|shadow|sudoers)\b", re.IGNORECASE),
    re.compile(r"\b>+\s*/etc/(passwd|shadow|sudoers)\b", re.IGNORECASE),
    re.compile(r"\b>+\s*/dev/(sda|sdb|sdc|mem|kmem)\b", re.IGNORECASE),
    re.compile(r"\bexport\s+PATH=.*\.\.\.", re.IGNORECASE),
    re.compile(r"\bgit\s+push\s+.*--force", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\s+-f[dx]*\b", re.IGNORECASE),
    re.compile(r"\bgit\s+checkout\s+--\s+\.", re.IGNORECASE),
    re.compile(r"\bgit\s+(filter-branch|reflog\s+expire|gc\s+--prune=now)\b", re.IGNORECASE),
    re.compile(r"\bgit\s+branch\s+-D\b", re.IGNORECASE),
    re.compile(r"\bgit\s+tag\s+-d\b", re.IGNORECASE),
    re.compile(r"\bmv\s+/\s+", re.IGNORECASE),
    re.compile(r"\bmkdir\s+-p\s+/\s*$", re.IGNORECASE),
    re.compile(r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+/(etc|usr|bin|boot|var|root|home|opt|srv)\b", re.IGNORECASE),
    re.compile(r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+~?\s*$", re.IGNORECASE),
    re.compile(r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+\.git\b", re.IGNORECASE),
    re.compile(r"\bgit\s+config\s+--global\s+user\.(email|name)\s+", re.IGNORECASE),
    re.compile(r"\b(?:minerd|xmrig|cpuminer|cryptonight)\b", re.IGNORECASE),
    re.compile(r"\b(?:docker|kubectl|helm|terraform|ansible-playbook)\s+.*\b(delete|rm|destroy)\b", re.IGNORECASE),
    re.compile(r"\bdocker\s+run\s+--privileged\b", re.IGNORECASE),
    re.compile(r"\bsysctl\s+-w\s+", re.IGNORECASE),
    re.compile(r"\becho\s+[0-9]\s+>\s*/proc/sys/", re.IGNORECASE),
    re.compile(r"\btouch\s+/etc/ld\.so\.preload\b", re.IGNORECASE),
    re.compile(r"\bsetfacl\s+-m\s+[^\s]+\s+/etc/", re.IGNORECASE),
    re.compile(r"\bssh\s+-o\s+StrictHostKeyChecking=no\s", re.IGNORECASE),
    re.compile(r"\bscp\s+.*root@", re.IGNORECASE),
    re.compile(r"\b(pip|pip3)\s+install\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+install\s+-g\b", re.IGNORECASE),
    re.compile(r"\b(apt-get|apt|yum|dnf|pacman|brew)\s+install\b", re.IGNORECASE),
    re.compile(r"\bumount\s+/", re.IGNORECASE),
    re.compile(r"\bmount\s+-o\s+remount", re.IGNORECASE),
    re.compile(r"\bswapoff\s+-a\b|\bswapon\s+/dev/", re.IGNORECASE),
    re.compile(r"\brfkill\s+block\b", re.IGNORECASE),
)

#: 高危二进制（出现在 argv[0] 或命令开头）。
DANGEROUS_BINARIES: frozenset[str] = frozenset(
    {
        "mkfs", "fdisk", "parted", "dd", "shutdown", "reboot", "poweroff",
        "halt", "init", "telinit", "iptables", "nft", "ufw", "modprobe",
        "insmod", "rmmod", "cryptsetup", "lvremove", "pvremove", "vgremove",
        "userdel", "groupdel", "chpasswd", "grub-mkconfig", "update-grub",
        "openssl", "socat", "nc", "ncat", "netcat", "minerd", "xmrig",
    }
)

#: 危险目标路径（正则片段）。
DANGEROUS_TARGETS: tuple[str, ...] = (
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/dev/sda",
    "/dev/sdb",
    "/dev/mem",
    "/dev/kmem",
    "/proc/kcore",
    "/etc/ld.so.preload",
)


def shell_words(command: str | Sequence[str]) -> list[str]:
    """把命令拆成词元（兼容字符串与 argv 列表）。

    :param command: 命令行字符串或 argv。
    :returns: 词元列表。
    """
    if isinstance(command, str):
        try:
            return shlex.split(command)
        except ValueError:
            return command.split()
    return list(command)


def match_dangerous_pattern(command: str) -> re.Match[str] | None:
    """正则层匹配。

    :param command: 拼接后的命令行。
    :returns: 命中的模式；未命中返回 ``None``。
    """
    for pattern in DANGEROUS_PATTERNS:
        match = pattern.search(command)
        if match is not None:
            return match
    return None


def decode_obfuscation(words: Sequence[str]) -> list[str]:
    """AST 层解码：识别 base64 / hex 混淆负载并解码。

    :param words: 拆词后的词元。
    :returns: 解码后的明文片段列表。
    """
    decoded: list[str] = []
    for index, word in enumerate(words):
        lower = word.lower()
        # base64 -d <<< '...' 或 echo ... | base64 -d | sh
        if "base64" in lower:
            # 候选负载可能在 base64 后面（跳过 -d 旗标）或前面（echo X | base64）。
            candidates: list[str] = []
            if index + 1 < len(words):
                candidates.append(words[index + 1])
            if index + 2 < len(words) and words[index + 1] in ("-d", "--decode", "-D"):
                candidates.append(words[index + 2])
            if index - 1 >= 0:
                candidates.append(words[index - 1])
            for candidate in candidates:
                candidate = candidate.strip("'\"")
                if not candidate or "=" in candidate or "|" in candidate:
                    continue
                try:
                    raw = base64.b64decode(candidate, validate=False)
                    text = raw.decode("utf-8", errors="ignore")
                    if any(
                        term in text.lower()
                        for term in ("rm ", "curl", "wget", "bash", "sh ", "chmod", "mkfs", "dd ")
                    ):
                        decoded.append(text)
                except Exception:  # noqa: BLE001 —— 解码失败不是危险证据
                    pass
        # 0x 十六进制混淆。
        if re.fullmatch(r"(0x[0-9a-fA-F]{2})+", word):
            try:
                raw = bytes.fromhex(word.replace("0x", ""))
                text = raw.decode("utf-8", errors="ignore")
                decoded.append(text)
            except Exception:  # noqa: BLE001
                pass
    return decoded


def is_dangerous_shell(command: str | Sequence[str]) -> tuple[bool, str]:
    """判定命令行是否危险（正则 + AST 双重判定）。

    :param command: 命令行字符串或 argv。
    :returns: ``(是否危险, 证据)``。
    """
    text = command if isinstance(command, str) else " ".join(command)
    match = match_dangerous_pattern(text)
    if match is not None:
        return True, f"命中危险命令模式：{match.group(0)[:120]}"
    words = shell_words(command)
    if not words:
        return False, ""
    base = words[0].split("/")[-1].lower()
    if base in DANGEROUS_BINARIES:
        return True, f"高危二进制：{base}"
    for target in DANGEROUS_TARGETS:
        if target in text:
            return True, f"命中危险目标路径：{target}"
    decoded = decode_obfuscation(words)
    if decoded:
        for payload in decoded:
            if match_dangerous_pattern(payload) is not None:
                return True, f"混淆解码后命中危险模式：{payload[:120]}"
            if "curl" in payload or "wget" in payload or "bash" in payload:
                return True, f"混淆解码后疑似下载执行：{payload[:120]}"
    return False, ""
