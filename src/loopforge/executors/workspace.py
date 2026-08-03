"""工作区管理 —— 目录隔离、配额、快照，以及宿主机文件原语。

两个执行器（local / docker）共用同一套**宿主机侧**文件操作：Docker 场景下
工作区是 bind mount，在宿主机直接读写比 ``docker cp`` 快一个数量级，也避免了
容器内外权限错配。因此本模块同时承担「工作区生命周期」与「文件原语」两职。

隔离层级::

    <workspace_root>/
      run-<run_id>/               # kind=run
      task-<run_id>-<task_id>/    # kind=task
      scratch-<run_id>/           # kind=scratch
      .snapshots/<handle_id>/<name>/

Windows 注意事项：``shutil.rmtree`` 遇到只读文件（``.git/objects`` 下大量存在）
会抛 ``PermissionError``，必须挂 ``onexc`` 回调先 ``chmod`` 再删。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from loopforge.constants import DEFAULT_ENCODING
from loopforge.core.clock import Clock, get_clock
from loopforge.core.hashing import sha256_hex
from loopforge.core.ids import new_ulid
from loopforge.core.logging import get_logger
from loopforge.executors.base import (
    DEFAULT_MAX_READ_BYTES,
    DirEntry,
    DirListing,
    FileContent,
    FileStat,
    WorkspaceHandle,
    WorkspaceNotReadyError,
    WorkspaceSpec,
)
from loopforge.executors.path_sandbox import PathSandbox, normalize_relative

__all__ = [
    "DEFAULT_IGNORED_DIRS",
    "MAX_LISTING_ENTRIES",
    "BINARY_SNIFF_BYTES",
    "WorkspaceManager",
    "write_text_atomic",
    "read_text_limited",
    "stat_file",
    "walk_listing",
    "directory_size",
    "remove_tree",
    "looks_binary",
]

_log = get_logger(__name__)

#: 列目录 / 搜索时默认跳过的目录。
DEFAULT_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        "dist",
        "build",
        ".idea",
        ".vscode",
        "target",
        ".next",
        ".turbo",
        ".gradle",
    }
)

#: 单次列目录返回的最大条目数。
MAX_LISTING_ENTRIES: int = 2000

#: 二进制嗅探读取的字节数。
BINARY_SNIFF_BYTES: int = 8192


# ---------------------------------------------------------------------------
# 宿主机文件原语
# ---------------------------------------------------------------------------


def looks_binary(raw: bytes) -> bool:
    """粗判字节流是否为二进制。

    Args:
        raw: 文件头部若干字节。

    Returns:
        ``True`` 表示疑似二进制（含 NUL 或高比例不可打印字符）。
    """
    if not raw:
        return False
    if b"\x00" in raw:
        return True
    printable = sum(1 for byte in raw if 32 <= byte < 127 or byte in (9, 10, 13))
    return printable / len(raw) < 0.75


def write_text_atomic(
    target: Path, content: str, *, encoding: str = DEFAULT_ENCODING, create_dirs: bool = True
) -> FileStat:
    """原子写入文本文件（同目录临时文件 + ``os.replace``）。

    同目录写临时文件是必要的：跨卷 rename 不是原子操作，
    ``TMPDIR`` 与工作区常常不在同一个卷上。

    Args:
        target: 目标绝对路径。
        content: 文本内容。
        encoding: 编码。
        create_dirs: 是否自动创建父目录。

    Returns:
        含 ``sha256`` 的 :class:`FileStat`。

    Raises:
        WorkspaceNotReadyError: 父目录不存在且 ``create_dirs=False``。
    """
    parent = target.parent
    if create_dirs:
        parent.mkdir(parents=True, exist_ok=True)
    elif not parent.is_dir():
        raise WorkspaceNotReadyError(
            f"父目录不存在：{parent}", details={"path": str(target), "reason": "no_parent"}
        )

    existed = target.exists()
    payload = content.encode(encoding, errors="strict")
    tmp = parent / f".lf-tmp-{new_ulid()}"
    try:
        with tmp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(target))
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    info = target.stat()
    return FileStat(
        path=str(target),
        size_bytes=info.st_size,
        sha256=sha256_hex(payload),
        is_dir=False,
        exists=True,
        encoding=encoding,
        mtime=datetime.fromtimestamp(info.st_mtime).astimezone(),
        created=not existed,
    )


def read_text_limited(
    target: Path, *, max_bytes: int = DEFAULT_MAX_READ_BYTES, encoding: str = DEFAULT_ENCODING
) -> FileContent:
    """读取文本文件，超过 ``max_bytes`` 截断。

    Args:
        target: 目标绝对路径。
        max_bytes: 最多读取字节数。
        encoding: 首选编码；解码失败时回退 ``utf-8`` + ``replace``。

    Returns:
        :class:`FileContent`。

    Raises:
        WorkspaceNotReadyError: 文件不存在或是目录。
    """
    if not target.exists():
        raise WorkspaceNotReadyError(
            f"文件不存在：{target}", details={"path": str(target), "reason": "not_found"}
        )
    if target.is_dir():
        raise WorkspaceNotReadyError(
            f"目标是目录而非文件：{target}", details={"path": str(target), "reason": "is_dir"}
        )

    total = target.stat().st_size
    with target.open("rb") as handle:
        raw = handle.read(max(max_bytes, 0))
    truncated = total > len(raw)
    binary = looks_binary(raw[:BINARY_SNIFF_BYTES])

    if binary:
        return FileContent(
            path=str(target),
            content="",
            encoding=encoding,
            size_bytes=total,
            sha256=sha256_hex(raw),
            truncated=truncated,
            line_count=0,
            is_binary=True,
        )

    try:
        text = raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        text = raw.decode("utf-8", errors="replace")
    return FileContent(
        path=str(target),
        content=text,
        encoding=encoding,
        size_bytes=total,
        sha256=sha256_hex(raw),
        truncated=truncated,
        line_count=text.count("\n") + (1 if text and not text.endswith("\n") else 0),
        is_binary=False,
    )


def stat_file(target: Path, *, encoding: str = DEFAULT_ENCODING) -> FileStat:
    """采集文件元信息（含内容 sha256），供幂等探测比对。

    Args:
        target: 绝对路径。
        encoding: 记录用的编码标记。

    Returns:
        :class:`FileStat`；文件不存在时 ``exists=False``。
    """
    if not target.exists():
        return FileStat(path=str(target), exists=False, sha256="", encoding=encoding)
    info = target.stat()
    if target.is_dir():
        return FileStat(
            path=str(target),
            size_bytes=0,
            sha256="",
            is_dir=True,
            exists=True,
            encoding=encoding,
            mtime=datetime.fromtimestamp(info.st_mtime).astimezone(),
        )
    digest = sha256_hex(target.read_bytes())
    return FileStat(
        path=str(target),
        size_bytes=info.st_size,
        sha256=digest,
        is_dir=False,
        exists=True,
        encoding=encoding,
        mtime=datetime.fromtimestamp(info.st_mtime).astimezone(),
    )


def walk_listing(
    root: Path,
    start: Path,
    *,
    depth: int = 1,
    ignored: Iterable[str] = DEFAULT_IGNORED_DIRS,
    max_entries: int = MAX_LISTING_ENTRIES,
    include_hidden: bool = True,
) -> DirListing:
    """按深度遍历目录。

    Args:
        root: 工作区根（用于计算相对路径）。
        start: 起始目录绝对路径。
        depth: 递归深度，``1`` 表示只列当前层。
        ignored: 跳过的目录名集合。
        max_entries: 条目上限，超出置 ``truncated=True``。
        include_hidden: 是否包含以 ``.`` 开头的条目。

    Returns:
        :class:`DirListing`，条目按「目录优先 + 名称字典序」排序。

    Raises:
        WorkspaceNotReadyError: 起始目录不存在。
    """
    if not start.is_dir():
        raise WorkspaceNotReadyError(
            f"目录不存在：{start}", details={"path": str(start), "reason": "not_a_dir"}
        )

    skip = set(ignored)
    entries: list[DirEntry] = []
    truncated = False
    frontier: list[tuple[Path, int]] = [(start, 1)]

    while frontier:
        current, level = frontier.pop(0)
        try:
            children = sorted(
                current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except (PermissionError, OSError):  # pragma: no cover - 权限异常按空目录处理
            continue
        for child in children:
            if not include_hidden and child.name.startswith("."):
                continue
            if child.is_dir() and child.name in skip:
                continue
            if len(entries) >= max_entries:
                truncated = True
                break
            is_dir = child.is_dir()
            size = 0 if is_dir else _safe_size(child)
            entries.append(
                DirEntry(
                    name=child.name,
                    path=normalize_relative(child, root),
                    is_dir=is_dir,
                    size_bytes=size,
                    depth=level,
                )
            )
            if is_dir and level < depth:
                frontier.append((child, level + 1))
        if truncated:
            break

    return DirListing(
        path=normalize_relative(start, root),
        entries=entries,
        total=len(entries),
        truncated=truncated,
    )


def _safe_size(path: Path) -> int:
    """读取文件大小，失败返回 0。"""
    try:
        return path.stat().st_size
    except OSError:  # pragma: no cover - 竞态删除
        return 0


def directory_size(root: Path, *, ignored: Iterable[str] = ()) -> int:
    """递归统计目录字节数。

    Args:
        root: 目录。
        ignored: 跳过的目录名。

    Returns:
        总字节数；目录不存在返回 0。
    """
    if not root.is_dir():
        return 0
    skip = set(ignored)
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in skip]
        for name in filenames:
            total += _safe_size(Path(dirpath) / name)
    return total


def _force_remove(func: Callable[[str], None], path: str, _exc: BaseException) -> None:
    """``shutil.rmtree`` 的错误回调：清掉只读位后重试。"""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError:  # pragma: no cover - 已被其他进程占用
        _log.warning("workspace_remove_failed", path=path)


def remove_tree(target: Path) -> None:
    """删除目录树（Windows 只读文件安全）。

    Args:
        target: 目录路径；不存在时静默返回（**幂等**）。
    """
    if not target.exists():
        return
    shutil.rmtree(str(target), onexc=_force_remove)


# ---------------------------------------------------------------------------
# 工作区管理器
# ---------------------------------------------------------------------------


class WorkspaceManager:
    """工作区生命周期管理。

    Args:
        root: 全部工作区的父目录。
        clock: 时间源。
        default_quota_bytes: 未在 spec 中指定时使用的配额。
        allow_symlinks: 传给 :class:`PathSandbox`。
    """

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Clock | None = None,
        default_quota_bytes: int | None = None,
        allow_symlinks: bool = False,
    ) -> None:
        self._root: Path = Path(root).expanduser().resolve()
        self._clock: Clock = clock or get_clock()
        self._default_quota: int | None = default_quota_bytes
        self._allow_symlinks: bool = allow_symlinks
        self._handles: dict[str, WorkspaceHandle] = {}
        self._sandboxes: dict[str, PathSandbox] = {}
        self._by_slug: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 属性

    @property
    def root(self) -> Path:
        """工作区父目录。"""
        return self._root

    @property
    def snapshots_root(self) -> Path:
        """快照存放目录。"""
        return self._root / ".snapshots"

    def count(self) -> int:
        """当前活跃工作区数量。"""
        return len(self._handles)

    def list_handles(self) -> list[WorkspaceHandle]:
        """列出全部活跃句柄。"""
        return list(self._handles.values())

    # ------------------------------------------------------------------ 生命周期

    async def create(self, spec: WorkspaceSpec, *, executor_kind: str = "local_subprocess") -> WorkspaceHandle:
        """创建（或复用）工作区。

        Args:
            spec: 工作区申请单。
            executor_kind: 记录在句柄上的执行器种类。

        Returns:
            :class:`WorkspaceHandle`。

        Raises:
            WorkspaceNotReadyError: 目录创建失败或源目录不存在。
        """
        async with self._lock:
            slug = spec.slug
            if spec.reuse and slug in self._by_slug:
                existing = self._handles[self._by_slug[slug]]
                if Path(existing.root).is_dir():
                    return existing
                self._forget(existing.id)

            directory = self._root / slug
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise WorkspaceNotReadyError(
                    f"工作区目录创建失败：{directory}",
                    details={"path": str(directory), "cause": str(exc)},
                ) from exc

            if spec.source_dir:
                await asyncio.to_thread(self._seed_from_source, spec, directory)

            handle = WorkspaceHandle(
                id=new_ulid(),
                root=str(directory.resolve()),
                run_id=spec.run_id,
                task_id=spec.task_id,
                kind=spec.kind,
                executor_kind=executor_kind,
                quota_bytes=spec.quota_bytes if spec.quota_bytes is not None else self._default_quota,
                env=dict(spec.env),
                created_at=self._clock.now(),
            )
            self._handles[handle.id] = handle
            self._by_slug[slug] = handle.id
            self._sandboxes[handle.id] = PathSandbox(
                handle.root, allow_symlinks=self._allow_symlinks
            )
            _log.info(
                "workspace_created",
                handle_id=handle.id,
                run_id=handle.run_id,
                task_id=handle.task_id,
                root=handle.root,
            )
            return handle

    def _seed_from_source(self, spec: WorkspaceSpec, directory: Path) -> None:
        """从 ``source_dir`` 复制初始内容（在线程池中执行）。

        Raises:
            WorkspaceNotReadyError: 源目录不存在。
        """
        source = Path(str(spec.source_dir)).expanduser().resolve()
        if not source.is_dir():
            raise WorkspaceNotReadyError(
                f"source_dir 不存在：{source}",
                details={"source_dir": str(source), "reason": "not_found"},
            )
        ignore = None if spec.copy_git_dir else shutil.ignore_patterns(".git")
        shutil.copytree(str(source), str(directory), dirs_exist_ok=True, ignore=ignore)

    async def destroy(self, handle: WorkspaceHandle) -> None:
        """销毁工作区（**幂等**）。

        Args:
            handle: 工作区句柄。
        """
        async with self._lock:
            target = Path(handle.root)
            await asyncio.to_thread(remove_tree, target)
            await asyncio.to_thread(remove_tree, self.snapshots_root / handle.id)
            self._forget(handle.id)
            _log.info("workspace_destroyed", handle_id=handle.id, root=handle.root)

    def _forget(self, handle_id: str) -> None:
        """从内部索引里摘掉句柄。"""
        self._handles.pop(handle_id, None)
        self._sandboxes.pop(handle_id, None)
        for slug, hid in list(self._by_slug.items()):
            if hid == handle_id:
                self._by_slug.pop(slug, None)

    async def destroy_all(self) -> int:
        """销毁全部工作区。

        Returns:
            被销毁的数量。
        """
        handles = list(self._handles.values())
        for handle in handles:
            await self.destroy(handle)
        return len(handles)

    # ------------------------------------------------------------------ 查询

    def get(self, handle_id: str) -> WorkspaceHandle:
        """按 ID 取句柄。

        Args:
            handle_id: 句柄 ID。

        Returns:
            :class:`WorkspaceHandle`。

        Raises:
            WorkspaceNotReadyError: 句柄未知或目录已消失。
        """
        handle = self._handles.get(handle_id)
        if handle is None:
            raise WorkspaceNotReadyError(
                f"未知的工作区句柄：{handle_id}",
                details={"handle_id": handle_id, "reason": "unknown_handle"},
            )
        if not Path(handle.root).is_dir():
            raise WorkspaceNotReadyError(
                f"工作区目录已消失：{handle.root}",
                details={"handle_id": handle_id, "root": handle.root, "reason": "root_missing"},
            )
        return handle

    def sandbox(self, handle: WorkspaceHandle | str) -> PathSandbox:
        """取（或惰性建）该工作区的路径沙箱。

        Args:
            handle: 句柄或句柄 ID。

        Returns:
            :class:`PathSandbox`。

        Raises:
            WorkspaceNotReadyError: 句柄未知。
        """
        if isinstance(handle, str):
            handle = self.get(handle)
        cached = self._sandboxes.get(handle.id)
        if cached is not None:
            return cached
        created = PathSandbox(handle.root, allow_symlinks=self._allow_symlinks)
        self._sandboxes[handle.id] = created
        return created

    def adopt(self, handle: WorkspaceHandle) -> WorkspaceHandle:
        """接管一个外部构造的句柄（跨进程恢复 / 测试用）。

        Args:
            handle: 已存在目录的句柄。

        Returns:
            被接管的句柄。

        Raises:
            WorkspaceNotReadyError: 目录不存在。
        """
        if not Path(handle.root).is_dir():
            raise WorkspaceNotReadyError(
                f"接管失败，目录不存在：{handle.root}",
                details={"handle_id": handle.id, "root": handle.root},
            )
        self._handles[handle.id] = handle
        self._sandboxes[handle.id] = PathSandbox(
            handle.root, allow_symlinks=self._allow_symlinks
        )
        return handle

    # ------------------------------------------------------------------ 配额与快照

    async def usage_bytes(self, handle: WorkspaceHandle) -> int:
        """统计工作区占用字节数。

        Args:
            handle: 句柄。

        Returns:
            字节数。
        """
        return await asyncio.to_thread(directory_size, Path(handle.root))

    async def enforce_quota(self, handle: WorkspaceHandle) -> tuple[bool, int]:
        """检查是否超配额。

        Args:
            handle: 句柄。

        Returns:
            ``(是否在配额内, 当前用量)``；未设配额时永远在配额内。
        """
        used = await self.usage_bytes(handle)
        if handle.quota_bytes is None:
            return True, used
        return used <= handle.quota_bytes, used

    async def snapshot(self, handle: WorkspaceHandle, name: str) -> str:
        """给工作区打快照。

        Args:
            handle: 句柄。
            name: 快照名（同名覆盖）。

        Returns:
            快照目录的绝对路径。
        """
        destination = self.snapshots_root / handle.id / name
        await asyncio.to_thread(remove_tree, destination)
        await asyncio.to_thread(
            shutil.copytree, str(handle.root), str(destination), dirs_exist_ok=True
        )
        _log.info("workspace_snapshot", handle_id=handle.id, name=name)
        return str(destination)

    async def restore(self, handle: WorkspaceHandle, name: str) -> None:
        """从快照恢复工作区。

        Args:
            handle: 句柄。
            name: 快照名。

        Raises:
            WorkspaceNotReadyError: 快照不存在。
        """
        source = self.snapshots_root / handle.id / name
        if not source.is_dir():
            raise WorkspaceNotReadyError(
                f"快照不存在：{name}",
                details={"handle_id": handle.id, "snapshot": name, "reason": "not_found"},
            )
        target = Path(handle.root)
        await asyncio.to_thread(remove_tree, target)
        await asyncio.to_thread(shutil.copytree, str(source), str(target), dirs_exist_ok=True)
        _log.info("workspace_restored", handle_id=handle.id, name=name)

    def list_snapshots(self, handle: WorkspaceHandle) -> list[str]:
        """列出该工作区的全部快照名。

        Args:
            handle: 句柄。

        Returns:
            快照名列表（字典序）。
        """
        base = self.snapshots_root / handle.id
        if not base.is_dir():
            return []
        return sorted(item.name for item in base.iterdir() if item.is_dir())

    def describe(self) -> dict[str, Any]:
        """导出管理器概览（healthcheck 用）。"""
        return {
            "root": str(self._root),
            "active": len(self._handles),
            "snapshots_root": str(self.snapshots_root),
        }

    def __repr__(self) -> str:
        return f"WorkspaceManager(root={str(self._root)!r}, active={len(self._handles)})"
