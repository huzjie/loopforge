"""路径沙箱 —— 执行层的第一道闸。

威胁模型
--------
LLM 生成的路径是**不可信输入**。真实观测到的逃逸手法至少有这些族：

======================  =====================================================
族                      样例
======================  =====================================================
相对穿越                ``../../etc/passwd``、``a/../../..``、``.//..//..``
绝对路径                ``/etc/shadow``、``C:\\Windows\\System32\\config\\SAM``
盘符相对                ``C:foo``（Windows 上等价于「C 盘当前目录下的 foo」）
UNC / 设备路径          ``\\\\server\\share``、``\\\\?\\C:\\``、``\\\\.\\PhysicalDrive0``
家目录展开              ``~``、``~/.ssh/id_rsa``、``~root/.bashrc``
URL 编码                ``%2e%2e%2f``、``..%2f``、``%252e%252e``（双重编码）
符号链接                workspace 内的 ``link -> /etc``
NUL 截断                ``ok.txt\\x00/../../etc/passwd``
Windows 保留设备名      ``CON``、``NUL``、``COM1``、``LPT1``
备用数据流              ``file.txt:secret``（NTFS ADS）
======================  =====================================================

设计
----
:class:`PathSandbox` 把「相对路径字符串」翻译成「保证落在 root 之内的绝对
``Path``」，任何不满足的输入一律抛 :class:`~loopforge.executors.base.PathEscapeError`
（``LF-EXECUTOR-0801``）。

**核心不变量**：``normcase(realpath(result))`` 必须以 ``normcase(realpath(root))``
为前缀。realpath 会解析符号链接，所以「workspace 内的软链指向外部」也会被前缀
比较兜住 —— 这是本模块能防住 symlink 逃逸的根本原因。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote

from loopforge.executors.base import PathEscapeError

__all__ = [
    "WINDOWS_RESERVED_NAMES",
    "MAX_PATH_LENGTH",
    "MAX_PATH_DEPTH",
    "PathSandbox",
    "is_windows",
    "normalize_relative",
]

#: Windows 保留设备名 —— 打开它们会命中设备而不是文件。
WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

#: 单个路径字符串的最大长度（防 DoS 与 Windows MAX_PATH 相关的诡异行为）。
MAX_PATH_LENGTH: int = 4096

#: 最大路径层级（防止 ``a/a/a/...`` 拖垮 realpath）。
MAX_PATH_DEPTH: int = 64

#: 形如 ``%2e`` / ``%252e`` 的百分号编码片段。
_PERCENT_ENCODED = re.compile(r"%[0-9a-fA-F]{2}")

#: 穿越语义的判定：任意一段等于 ``..``。
_DOTDOT_SEGMENT = re.compile(r"(^|[\\/])\.\.([\\/]|$)")


def is_windows() -> bool:
    """当前是否运行在 Windows 上。"""
    return os.name == "nt"


def normalize_relative(path: Path, root: Path) -> str:
    """把绝对路径转成相对 ``root`` 的 POSIX 风格字符串。

    Args:
        path: 绝对路径。
        root: 工作区根。

    Returns:
        形如 ``src/loopforge/app.py`` 的相对路径；``path == root`` 时返回 ``"."``。
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        # 大小写差异导致 relative_to 失败时退回字符串裁剪。
        text = os.path.normcase(str(path))
        prefix = os.path.normcase(str(root))
        trimmed = text[len(prefix) :].lstrip("\\/")
        return PurePosixPath(trimmed.replace("\\", "/")).as_posix() if trimmed else "."
    as_posix = rel.as_posix()
    return as_posix if as_posix not in ("", ".") else "."


class PathSandbox:
    """把不可信路径钉死在一个根目录内。

    Args:
        root: 工作区根目录。构造时会 ``realpath`` 归一化。
        allow_symlinks: 是否允许路径中出现符号链接（即便其目标在 root 内）。
            默认 ``False`` —— 长程 Agent 场景里，软链只会带来惊喜，不会带来价值。
        extra_roots: 额外允许访问的根（例如只读的依赖缓存目录）。
        allow_hidden: 是否允许访问以 ``.`` 开头的条目（``.git`` 必须允许，
            所以默认 ``True``；设为 ``False`` 可用于更严格的只读工具）。

    Example:
        >>> import tempfile, pathlib
        >>> d = tempfile.mkdtemp()
        >>> sb = PathSandbox(d)
        >>> sb.resolve("a/b.txt").name
        'b.txt'
        >>> sb.resolve("../../etc/passwd")          # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        loopforge.executors.base.PathEscapeError: ...
    """

    __slots__ = ("_root", "_root_key", "_allow_symlinks", "_extra_roots", "_allow_hidden")

    def __init__(
        self,
        root: str | Path,
        *,
        allow_symlinks: bool = False,
        extra_roots: Sequence[str | Path] = (),
        allow_hidden: bool = True,
    ) -> None:
        self._root: Path = self._realpath(Path(root))
        self._root_key: str = os.path.normcase(str(self._root))
        self._allow_symlinks: bool = allow_symlinks
        self._allow_hidden: bool = allow_hidden
        self._extra_roots: tuple[tuple[Path, str], ...] = tuple(
            (p, os.path.normcase(str(p)))
            for p in (self._realpath(Path(item)) for item in extra_roots)
        )

    # ------------------------------------------------------------------ 属性

    @property
    def root(self) -> Path:
        """归一化后的工作区根。"""
        return self._root

    @property
    def allow_symlinks(self) -> bool:
        """是否允许符号链接。"""
        return self._allow_symlinks

    # ------------------------------------------------------------------ 主接口

    def resolve(self, candidate: str | Path, *, must_exist: bool = False) -> Path:
        """把不可信路径解析成绝对路径，并保证其位于沙箱内。

        Args:
            candidate: 相对（推荐）或绝对路径。
            must_exist: 为 ``True`` 时目标必须已存在。

        Returns:
            位于 ``root`` 之内的绝对 :class:`pathlib.Path`。

        Raises:
            PathEscapeError: 命中任意一种逃逸手法，或目标不在 root 内。
        """
        raw = str(candidate)
        self._reject_malformed(raw)
        self._reject_encoded_traversal(raw)
        self._reject_home_expansion(raw)

        target = self._to_absolute(raw)
        self._reject_reserved_device(target)

        resolved = self._realpath(target)
        self._assert_inside(resolved, raw)

        if not self._allow_symlinks:
            self._assert_no_symlink_component(target, raw)
        if not self._allow_hidden:
            self._assert_no_hidden_component(resolved, raw)
        if must_exist and not resolved.exists():
            raise PathEscapeError(
                f"路径不存在：{raw}",
                details={"path": raw, "resolved": str(resolved), "reason": "not_found"},
            )
        return resolved

    def resolve_dir(self, candidate: str | Path, *, must_exist: bool = True) -> Path:
        """解析并要求结果是目录。

        Args:
            candidate: 待解析路径。
            must_exist: 目录是否必须已存在。

        Returns:
            目录的绝对路径。

        Raises:
            PathEscapeError: 逃逸、不存在或不是目录。
        """
        resolved = self.resolve(candidate, must_exist=must_exist)
        if resolved.exists() and not resolved.is_dir():
            raise PathEscapeError(
                f"目标不是目录：{candidate}",
                details={"path": str(candidate), "reason": "not_a_directory"},
            )
        return resolved

    def is_inside(self, candidate: str | Path) -> bool:
        """判定路径是否在沙箱内（不抛异常的版本）。

        Args:
            candidate: 待判定路径。

        Returns:
            ``True`` 表示安全。
        """
        try:
            self.resolve(candidate)
        except PathEscapeError:
            return False
        return True

    def relative(self, absolute: str | Path) -> str:
        """把沙箱内的绝对路径转回 POSIX 相对路径。

        Args:
            absolute: 绝对路径。

        Returns:
            相对 ``root`` 的 POSIX 路径字符串。
        """
        return normalize_relative(Path(absolute), self._root)

    def validate_many(self, candidates: Iterable[str | Path]) -> list[Path]:
        """批量解析；任意一个失败即整体失败（fail-fast）。

        Args:
            candidates: 路径集合。

        Returns:
            解析后的绝对路径列表，顺序与输入一致。

        Raises:
            PathEscapeError: 任意一个路径越界。
        """
        return [self.resolve(item) for item in candidates]

    def ensure_parent(self, candidate: str | Path) -> Path:
        """解析路径并确保其父目录存在（父目录同样受沙箱约束）。

        Args:
            candidate: 目标文件路径。

        Returns:
            目标文件的绝对路径。

        Raises:
            PathEscapeError: 越界。
        """
        resolved = self.resolve(candidate)
        parent = resolved.parent
        self._assert_inside(parent, str(candidate))
        parent.mkdir(parents=True, exist_ok=True)
        return resolved

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _realpath(path: Path) -> Path:
        """``os.path.realpath`` 包装：解析符号链接 / junction，且不要求存在。"""
        return Path(os.path.realpath(str(path)))

    @staticmethod
    def _reject_malformed(raw: str) -> None:
        """拒绝空串、超长、含 NUL 或控制字符的路径。

        Raises:
            PathEscapeError: 输入畸形。
        """
        if raw == "":
            raise PathEscapeError("路径为空", details={"path": raw, "reason": "empty"})
        if "\x00" in raw:
            raise PathEscapeError(
                "路径含 NUL 字节（截断攻击）", details={"path": repr(raw), "reason": "nul_byte"}
            )
        if len(raw) > MAX_PATH_LENGTH:
            raise PathEscapeError(
                f"路径过长（{len(raw)} > {MAX_PATH_LENGTH}）",
                details={"length": len(raw), "reason": "too_long"},
            )
        if any(ord(ch) < 32 for ch in raw):
            raise PathEscapeError(
                "路径含控制字符", details={"path": repr(raw), "reason": "control_char"}
            )
        depth = len([seg for seg in re.split(r"[\\/]+", raw) if seg not in ("", ".")])
        if depth > MAX_PATH_DEPTH:
            raise PathEscapeError(
                f"路径层级过深（{depth} > {MAX_PATH_DEPTH}）",
                details={"depth": depth, "reason": "too_deep"},
            )

    @staticmethod
    def _reject_encoded_traversal(raw: str) -> None:
        """拒绝 URL 编码（含多重编码）的穿越。

        直接 ``unquote`` 最多三轮；只要任何一轮里出现 ``..`` 段或根前缀就拒绝。
        注意：合法文件名里出现字面 ``%2e`` 的概率远低于这是一次攻击的概率。

        Raises:
            PathEscapeError: 检测到编码穿越。
        """
        if not _PERCENT_ENCODED.search(raw):
            return
        current = raw
        for _ in range(3):
            decoded = unquote(current)
            if decoded == current:
                break
            current = decoded
            if _DOTDOT_SEGMENT.search(current) or current.startswith(("/", "\\", "~")):
                raise PathEscapeError(
                    "路径含 URL 编码的穿越序列",
                    details={"path": raw, "decoded": current, "reason": "encoded_traversal"},
                )
        if _PERCENT_ENCODED.search(raw) and _DOTDOT_SEGMENT.search(unquote(raw)):
            raise PathEscapeError(
                "路径含 URL 编码的穿越序列",
                details={"path": raw, "reason": "encoded_traversal"},
            )

    @staticmethod
    def _reject_home_expansion(raw: str) -> None:
        """拒绝 ``~`` / ``~user`` 家目录展开。

        Raises:
            PathEscapeError: 以 ``~`` 开头。
        """
        if raw.startswith("~"):
            raise PathEscapeError(
                "禁止家目录展开（~）", details={"path": raw, "reason": "home_expansion"}
            )

    def _to_absolute(self, raw: str) -> Path:
        """把原始字符串转成候选绝对路径。

        「是否绝对」必须同时按 POSIX 与 Windows 两套语义判断 ——
        ``PureWindowsPath("/etc/passwd").is_absolute()`` 在 Windows 上是 ``False``，
        只看 :meth:`pathlib.Path.is_absolute` 会漏掉这一族。

        Args:
            raw: 原始路径字符串。

        Returns:
            候选绝对路径（尚未做前缀校验）。
        """
        if self._is_rooted(raw):
            return Path(raw)
        return self._root / raw

    @staticmethod
    def _is_rooted(raw: str) -> bool:
        """判断字符串是否携带「根」语义（绝对路径 / 盘符 / UNC / 设备路径）。

        Args:
            raw: 原始路径字符串。

        Returns:
            ``True`` 表示不能简单地拼到 root 后面。
        """
        if raw.startswith(("/", "\\")):
            return True
        win = PureWindowsPath(raw)
        if win.is_absolute() or win.drive:
            return True
        return PurePosixPath(raw).is_absolute()

    @staticmethod
    def _reject_reserved_device(target: Path) -> None:
        """拒绝 Windows 保留设备名与 NTFS 备用数据流。

        Raises:
            PathEscapeError: 命中保留名或 ADS。
        """
        if not is_windows():
            return
        for part in target.parts:
            stem = part.split(".")[0].strip().upper()
            if stem in WINDOWS_RESERVED_NAMES:
                raise PathEscapeError(
                    f"路径命中 Windows 保留设备名：{part}",
                    details={"segment": part, "reason": "reserved_device"},
                )
            # 盘符本身形如 "C:\"，跳过；其余段里出现 ':' 即 NTFS ADS。
            if ":" in part and not (len(part) <= 3 and part.rstrip("\\/").endswith(":")):
                raise PathEscapeError(
                    f"路径含 NTFS 备用数据流：{part}",
                    details={"segment": part, "reason": "alternate_data_stream"},
                )

    def _assert_inside(self, resolved: Path, raw: str) -> None:
        """前缀校验 —— 沙箱的核心不变量。

        Raises:
            PathEscapeError: 解析结果落在 root（及 extra_roots）之外。
        """
        key = os.path.normcase(str(resolved))
        if self._matches(key, self._root_key):
            return
        for _, extra_key in self._extra_roots:
            if self._matches(key, extra_key):
                return
        raise PathEscapeError(
            f"路径逃逸出工作区：{raw}",
            details={
                "path": raw,
                "resolved": str(resolved),
                "root": str(self._root),
                "reason": "outside_root",
            },
        )

    @staticmethod
    def _matches(key: str, root_key: str) -> bool:
        """``key`` 是否等于 ``root_key`` 或位于其之下。"""
        if key == root_key:
            return True
        prefix = root_key if root_key.endswith(os.sep) else root_key + os.sep
        return key.startswith(prefix)

    def _assert_no_symlink_component(self, target: Path, raw: str) -> None:
        """逐段检查是否存在符号链接。

        即使软链目标仍在 root 内也拒绝：允许软链意味着「同一个文件有两个身份」，
        这会让基于 sha256 的幂等探测出现假阴性。

        Raises:
            PathEscapeError: 路径中任一段是符号链接。
        """
        current = self._root
        try:
            relative = Path(os.path.relpath(str(target), str(self._root)))
        except ValueError:  # pragma: no cover - 跨盘符，前缀校验已先行拦截
            return
        for part in relative.parts:
            if part in ("..", "."):
                continue
            current = current / part
            if current.is_symlink():
                raise PathEscapeError(
                    f"路径经过符号链接：{self.relative(current)}",
                    details={"path": raw, "symlink": str(current), "reason": "symlink"},
                )

    def _assert_no_hidden_component(self, resolved: Path, raw: str) -> None:
        """拒绝隐藏文件 / 目录。

        Raises:
            PathEscapeError: 任一段以 ``.`` 开头。
        """
        relative = Path(os.path.relpath(str(resolved), str(self._root)))
        for part in relative.parts:
            if part.startswith(".") and part not in (".", ".."):
                raise PathEscapeError(
                    f"禁止访问隐藏条目：{part}",
                    details={"path": raw, "segment": part, "reason": "hidden"},
                )

    def __repr__(self) -> str:
        return f"PathSandbox(root={str(self._root)!r}, allow_symlinks={self._allow_symlinks})"
