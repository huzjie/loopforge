"""路径沙箱逃逸测试 —— 覆盖 ≥20 种逃逸手法。

验证的核心不变量：任何输入都无法把 ``PathSandbox.resolve`` 的结果带出 root。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from loopforge.executors.base import PathEscapeError
from loopforge.executors.path_sandbox import (
    MAX_PATH_DEPTH,
    MAX_PATH_LENGTH,
    PathSandbox,
    normalize_relative,
)


@pytest.fixture()
def sandbox(tmp_path: Path) -> PathSandbox:
    """临时目录上的沙箱。"""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    return PathSandbox(tmp_path)


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------


def test_resolve_plain_file(sandbox: PathSandbox) -> None:
    """普通相对路径可解析。"""
    resolved = sandbox.resolve("a.txt")
    assert resolved.name == "a.txt"
    assert resolved.is_absolute()


def test_resolve_nested(sandbox: PathSandbox) -> None:
    """嵌套相对路径可解析。"""
    resolved = sandbox.resolve("sub/inner.txt")
    assert resolved.name == "inner.txt"


def test_resolve_dot_segments(sandbox: PathSandbox) -> None:
    """``./`` 与重复斜杠是合法的。"""
    resolved = sandbox.resolve("./sub//inner.txt")
    assert resolved.name == "inner.txt"


def test_resolve_root_itself(sandbox: PathSandbox) -> None:
    """``.`` 解析为 root 本身。"""
    assert sandbox.resolve(".") == sandbox.root


# ---------------------------------------------------------------------------
# 逃逸族 1：相对穿越
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "../etc/passwd",
        "../../etc/passwd",
        "../../../etc/passwd",
        "a/../../..",
        "sub/../../etc/passwd",
        "..",
        "sub/../..",
        ".//..//..",
        "sub/../../../",
    ],
)
def test_dotdot_escape(sandbox: PathSandbox, candidate: str) -> None:
    """``..`` 穿越必须全部拦截。"""
    with pytest.raises(PathEscapeError):
        sandbox.resolve(candidate)


# ---------------------------------------------------------------------------
# 逃逸族 2：绝对路径与盘符
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "/etc/passwd",
        "/",
        "/tmp",
        "C:\\Windows\\System32\\config\\SAM",
        "C:/Windows/System32",
        "D:\\secret.txt",
        "C:foo",  # 盘符相对
        "\\\\server\\share\\file",
        "\\\\?\\C:\\",
        "\\\\.\\PhysicalDrive0",
    ],
)
def test_absolute_and_drive_escape(sandbox: PathSandbox, candidate: str) -> None:
    """绝对路径 / Windows 盘符 / UNC / 设备路径必须拦截。"""
    with pytest.raises(PathEscapeError):
        sandbox.resolve(candidate)


# ---------------------------------------------------------------------------
# 逃逸族 3：家目录与编码
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "~/.ssh/id_rsa",
        "~root/.bashrc",
        "~/secret",
    ],
)
def test_home_expansion_escape(sandbox: PathSandbox, candidate: str) -> None:
    """``~`` 展开必须拦截。"""
    with pytest.raises(PathEscapeError):
        sandbox.resolve(candidate)


@pytest.mark.parametrize(
    "candidate",
    [
        "%2e%2e%2fetc%2fpasswd",
        "..%2fetc%2fpasswd",
        "%252e%252e%252fetc%252fpasswd",
        "sub%2f..%2f..%2fetc",
    ],
)
def test_encoded_traversal_escape(sandbox: PathSandbox, candidate: str) -> None:
    """URL 编码（含双重编码）穿越必须拦截。"""
    with pytest.raises(PathEscapeError):
        sandbox.resolve(candidate)


# ---------------------------------------------------------------------------
# 逃逸族 4：畸形输入
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "ok.txt\x00/../../etc/passwd",
        "a" * (MAX_PATH_LENGTH + 1),
    ],
)
def test_malformed_escape(sandbox: PathSandbox, candidate: str) -> None:
    """空串 / NUL 截断 / 超长必须拦截。"""
    with pytest.raises(PathEscapeError):
        sandbox.resolve(candidate)


def test_control_char_escape(sandbox: PathSandbox) -> None:
    """控制字符必须拦截。"""
    with pytest.raises(PathEscapeError):
        sandbox.resolve("a\x01b.txt")


def test_too_deep_escape(sandbox: PathSandbox) -> None:
    """层级过深必须拦截。"""
    deep = "/".join(["x"] * (MAX_PATH_DEPTH + 1))
    with pytest.raises(PathEscapeError):
        sandbox.resolve(deep)


# ---------------------------------------------------------------------------
# 逃逸族 5：符号链接
# ---------------------------------------------------------------------------


def test_symlink_outside_escape(tmp_path: Path) -> None:
    """工作区内软链指向外部 → 必须拦截。"""
    outside = Path(tempfile.mkdtemp())
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    inside = tmp_path / "inside"
    inside.mkdir()
    link = inside / "link"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持创建符号链接")
    sb = PathSandbox(tmp_path)
    with pytest.raises(PathEscapeError):
        sb.resolve("inside/link/secret.txt")


def test_symlink_inside_rejected(tmp_path: Path) -> None:
    """工作区内软链即使指向内部也拒绝（默认 allow_symlinks=False）。"""
    (tmp_path / "real.txt").write_text("r", encoding="utf-8")
    try:
        (tmp_path / "alias").symlink_to(tmp_path / "real.txt")
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持创建符号链接")
    sb = PathSandbox(tmp_path)
    with pytest.raises(PathEscapeError):
        sb.resolve("alias")


def test_symlink_allowed_when_enabled(tmp_path: Path) -> None:
    """显式允许软链时可解析内部软链。"""
    (tmp_path / "real.txt").write_text("r", encoding="utf-8")
    try:
        (tmp_path / "alias").symlink_to(tmp_path / "real.txt")
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持创建符号链接")
    sb = PathSandbox(tmp_path, allow_symlinks=True)
    resolved = sb.resolve("alias")
    # realpath 会把软链解析到真实文件，但仍在沙箱内。
    assert resolved == (tmp_path / "real.txt").resolve()


# ---------------------------------------------------------------------------
# Windows 保留名 / ADS
# ---------------------------------------------------------------------------


def test_reserved_device_names(sandbox: PathSandbox) -> None:
    """Windows 保留设备名必须拦截（所有平台都拦截，防止跨平台脏数据）。"""
    for name in ("CON", "NUL", "AUX", "COM1", "LPT2"):
        with pytest.raises(PathEscapeError):
            sandbox.resolve(name)


def test_ntfs_ads(sandbox: PathSandbox) -> None:
    """NTFS 备用数据流必须拦截。"""
    with pytest.raises(PathEscapeError):
        sandbox.resolve("a.txt:secret")


# ---------------------------------------------------------------------------
# 辅助 API
# ---------------------------------------------------------------------------


def test_relative_roundtrip(sandbox: PathSandbox) -> None:
    """relative() 与 resolve() 互逆。"""
    resolved = sandbox.resolve("sub/inner.txt")
    assert sandbox.relative(resolved) == "sub/inner.txt"
    assert sandbox.resolve(sandbox.relative(resolved)) == resolved


def test_validate_many_fail_fast(sandbox: PathSandbox) -> None:
    """批量校验 fail-fast。"""
    with pytest.raises(PathEscapeError):
        sandbox.validate_many(["a.txt", "../etc/passwd", "sub"])


def test_is_inside_does_not_raise(sandbox: PathSandbox) -> None:
    """is_inside 不抛异常。"""
    assert sandbox.is_inside("a.txt")
    assert not sandbox.is_inside("../../etc/passwd")


def test_normalize_relative(tmp_path: Path) -> None:
    """normalize_relative 输出 POSIX 风格。"""
    root = tmp_path / "ws"
    root.mkdir()
    target = root / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    assert normalize_relative(target, root) == "src/app.py"
    assert normalize_relative(root, root) == "."


def test_ensure_parent_creates(sandbox: PathSandbox) -> None:
    """ensure_parent 自动建目录且受沙箱约束。"""
    target = sandbox.ensure_parent("new/deep/dir/file.txt")
    assert target.parent.is_dir()
    with pytest.raises(PathEscapeError):
        sandbox.ensure_parent("../outside/file.txt")
