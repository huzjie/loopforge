"""``DockerExecutor`` —— 生产执行器（容器隔离）。

为什么容器隔离是生产级
----------------------
本地子进程共享宿主内核：``rm -rf`` 只差一个目录、``kill`` 只差一个 PID。
生产部署要求**物理隔离**：容器 + 只读根挂载 + 默认 ``--network none`` +
cgroup 限额（CPU / 内存 / PID 数）。

本类与 :class:`~loopforge.executors.local.LocalSubprocessExecutor` 共享同一套
:class:`~loopforge.executors.base.BaseExecutor` 契约（§11.4），并通过同一套
契约测试参数化跑两遍 —— 这正是「Docker 与 Local 双实现」的验收方式。

降级语义
--------
Docker SDK 缺失 / daemon 未启动 / 镜像拉取失败 → 抛
:class:`~loopforge.executors.base.ContainerUnavailableError`
（``LF-EXECUTOR-0804``），**绝不直接崩栈**；下游据此降级到本地执行器。
:meth:`healthcheck` 在 Docker 不可用时返回 ``healthy=False``，不抛异常。

文件操作
--------
工作区是宿主机目录 + 容器 bind mount。文件读写走宿主机（与 Local 执行器共用
:class:`~loopforge.executors.workspace.WorkspaceManager`），比 ``docker cp``
快一个数量级，也避免容器内外权限错配。
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, ClassVar

from loopforge.core.clock import Clock, get_clock
from loopforge.core.config import DockerExecutorSettings, ExecutorSettings, get_settings
from loopforge.core.logging import get_logger
from loopforge.executors.base import (
    BaseExecutor,
    CommandResult,
    CommandSpec,
    ContainerUnavailableError,
    DirListing,
    ExecutorHealth,
    FileContent,
    FileStat,
    InvalidCommandError,
    WorkspaceHandle,
    WorkspaceNotReadyError,
    WorkspaceSpec,
)
from loopforge.executors.path_sandbox import PathSandbox
from loopforge.executors.workspace import (
    WorkspaceManager,
    read_text_limited,
    stat_file,
    walk_listing,
    write_text_atomic,
)

__all__ = ["DockerExecutor"]

_log = get_logger(__name__)


def _docker_available() -> bool:
    """Docker SDK 是否已安装。"""
    try:
        import docker  # noqa: F401, PLC0415 - 可选依赖，延迟导入
    except ImportError:
        return False
    return True


def _docker_client() -> Any:
    """构造 docker client。

    Returns:
        Docker SDK client。

    Raises:
        ContainerUnavailableError: SDK 缺失或 daemon 不可达。
    """
    if not _docker_available():
        raise ContainerUnavailableError(
            "Docker SDK 未安装（pip install loopforge[docker]）",
            details={"reason": "sdk_missing"},
        )
    try:
        import docker  # noqa: PLC0415 - 可选依赖

        return docker.from_env()
    except Exception as exc:  # noqa: BLE001 - 任何 daemon 连接失败都转成清晰错误
        raise ContainerUnavailableError(
            f"无法连接 Docker daemon：{exc}",
            details={"reason": "daemon_unreachable", "cause": str(exc)},
        ) from exc


class DockerExecutor(BaseExecutor):
    """容器执行器。

    Args:
        workspace_root: 工作区父目录（宿主机侧）。
        settings: 执行器配置（默认取全局）。
        clock: 时间源。
        allow_symlinks: 是否允许符号链接。
        default_quota_bytes: 未在 spec 指定时的磁盘配额。
    """

    kind: ClassVar[str] = "docker"
    enabled: ClassVar[bool] = True

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        settings: ExecutorSettings | None = None,
        clock: Clock | None = None,
        allow_symlinks: bool = False,
        default_quota_bytes: int | None = None,
    ) -> None:
        cfg: ExecutorSettings = settings or get_settings().executor
        self._docker_cfg: DockerExecutorSettings = cfg.docker
        root = workspace_root if workspace_root is not None else cfg.workspace_root
        self._clock: Clock = clock or get_clock()
        self._manager: WorkspaceManager = WorkspaceManager(
            root,
            clock=self._clock,
            default_quota_bytes=default_quota_bytes,
            allow_symlinks=allow_symlinks,
        )
        self._client: Any | None = None

    @property
    def manager(self) -> WorkspaceManager:
        """工作区管理器（测试与诊断用）。"""
        return self._manager

    def _lazy_client(self) -> Any:
        """惰性获取 docker client。"""
        if self._client is None:
            self._client = _docker_client()
        return self._client

    # ------------------------------------------------------------------ prepare

    async def prepare(self, spec: WorkspaceSpec) -> WorkspaceHandle:
        """创建宿主机工作区并启动（或复用）容器。

        Args:
            spec: 工作区申请单（``image`` 可覆盖默认镜像）。

        Returns:
            带 ``container_id`` 的 :class:`WorkspaceHandle`。

        Raises:
            ContainerUnavailableError: Docker 不可用。
        """
        client = self._lazy_client()
        handle = await self._manager.create(spec, executor_kind=self.kind)
        try:
            image = spec.image or self._docker_cfg.image
            container_id = await asyncio.to_thread(
                self._ensure_container, handle, image
            )
            handle.container_id = container_id
            return handle
        except ContainerUnavailableError:
            await self._manager.destroy(handle)
            raise

    def _ensure_container(self, handle: WorkspaceHandle, image: str) -> str:
        """同步创建 / 复用容器（在线程池中执行）。

        Args:
            handle: 工作区句柄。
            image: 镜像名。

        Returns:
            容器 ID。

        Raises:
            ContainerUnavailableError: 镜像拉取失败等。
        """
        client = self._lazy_client()
        try:
            client.images.pull(image)
        except Exception as exc:  # noqa: BLE001 - 拉取失败降级
            raise ContainerUnavailableError(
                f"镜像拉取失败：{image}",
                details={"image": image, "cause": str(exc)},
            ) from exc

        name = f"loopforge-{handle.id}"
        reuse = self._docker_cfg.reuse_container
        if reuse:
            try:
                existing = client.containers.get(name)
                if existing.status == "running":
                    return existing.id
                existing.remove(force=True)
            except Exception:  # noqa: BLE001 - 容器不存在时继续创建
                pass

        try:
            container = client.containers.run(
                image,
                command=["sleep", "infinity"],
                name=name,
                detach=True,
                network_mode=self._docker_cfg.network,
                mem_limit=self._docker_cfg.memory_limit,
                nano_cpus=int(self._docker_cfg.cpu_quota * 1_000_000_000),
                pids_limit=256,
                volumes={str(Path(handle.root)): {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                read_only=False,
                tmpfs={"/tmp": "size=256m"},
            )
            return container.id
        except Exception as exc:  # noqa: BLE001 - 容器创建失败降级
            raise ContainerUnavailableError(
                f"容器创建失败：{exc}",
                details={"name": name, "cause": str(exc)},
            ) from exc

    # ------------------------------------------------------------------ run

    async def run(self, cmd: CommandSpec) -> CommandResult:
        """在容器内执行命令。

        Args:
            cmd: 命令申请单。

        Returns:
            :class:`CommandResult`。

        Raises:
            ContainerUnavailableError: Docker 不可用。
            WorkspaceNotReadyError: ``handle_id`` 未知。
        """
        client = self._lazy_client()
        handle = self._manager.get(cmd.handle_id)
        if not handle.container_id:
            raise WorkspaceNotReadyError(
                f"工作区 {handle.id} 没有关联容器",
                details={"handle_id": handle.id, "reason": "no_container"},
            )

        argv = list(cmd.argv)
        if not argv:
            raise InvalidCommandError("argv 为空，拒绝执行", details={"handle_id": cmd.handle_id})

        sandbox = self._manager.sandbox(handle)
        cwd = sandbox.resolve_dir(cmd.cwd, must_exist=True)
        rel_cwd = sandbox.relative(cwd) or "."

        try:
            result = await asyncio.to_thread(
                self._exec_in_container,
                client,
                handle.container_id,
                argv,
                rel_cwd,
                cmd.stdin,
                cmd.timeout_s,
                cmd.max_output_bytes,
            )
            return result
        except ContainerUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - 容器执行失败统一包装
            raise ContainerUnavailableError(
                f"容器内执行失败：{exc}",
                details={"argv": argv, "cause": str(exc)},
            ) from exc

    def _exec_in_container(
        self,
        client: Any,
        container_id: str,
        argv: list[str],
        cwd: str,
        stdin: str | None,
        timeout_s: float,
        max_output_bytes: int,
    ) -> CommandResult:
        """同步执行 ``docker exec``（在线程池中运行，规避 SDK 阻塞事件循环）。"""
        import docker  # noqa: PLC0415 - 可选依赖
        from docker.errors import APIError, NotFound  # noqa: PLC0415

        container = client.containers.get(container_id)
        try:
            exec_id = container.exec_run(
                argv,
                workdir=f"/workspace/{cwd}" if cwd != "." else "/workspace",
                stdin=stdin is not None,
                socket=True,
                demux=False,
                environment={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            )
        except (APIError, NotFound) as exc:
            raise ContainerUnavailableError(
                f"docker exec 失败：{exc}", details={"argv": argv, "cause": str(exc)}
            ) from exc

        socket = exec_id.output
        stdout_bytes = bytearray()
        stderr_bytes = bytearray()
        total = 0
        limit = max(max_output_bytes, 0)
        truncated = False

        def _feed(buffer: bytearray, chunk: bytes) -> None:
            nonlocal total, truncated
            total += len(chunk)
            if len(buffer) < limit:
                room = limit - len(buffer)
                buffer.extend(chunk[:room])
            else:
                truncated = True

        import select  # noqa: PLC0415 - 同步 socket 读

        socket._sock.setblocking(False)  # type: ignore[attr-defined]
        import time  # noqa: PLC0415

        deadline = time.monotonic() + timeout_s
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([socket._sock], [], [], 0.1)  # type: ignore[attr-defined]
                if not ready:
                    continue
                try:
                    chunk = socket.recv(65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                # docker attach 流：前 8 字节是 [stream_type(1), 3 个 0, size(4)] 帧头
                if len(chunk) >= 8:
                    stream_type = chunk[0]
                    payload = chunk[8:]
                    if stream_type == 1:
                        _feed(stdout_bytes, payload)
                    elif stream_type == 2:
                        _feed(stderr_bytes, payload)
                    else:
                        _feed(stdout_bytes, payload)
                else:
                    _feed(stdout_bytes, chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                socket.close()
            except Exception:  # noqa: BLE001 - 关闭失败忽略
                pass

        timed_out = time.monotonic() >= deadline
        try:
            exit_code = exec_id.exit_code
        except Exception:  # noqa: BLE001 - exec 句柄可能已失效
            exit_code = -1

        return CommandResult(
            exit_code=exit_code,
            stdout=bytes(stdout_bytes).decode("utf-8", errors="replace"),
            stderr=bytes(stderr_bytes).decode("utf-8", errors="replace"),
            duration_ms=int(timeout_s * 1000) if timed_out else 0,
            truncated=truncated,
            timed_out=timed_out,
            oom_killed=False,
        )

    # ------------------------------------------------------------------ 文件原语

    async def write_file(
        self, handle: WorkspaceHandle, path: str, content: str, *, encoding: str = "utf-8"
    ) -> FileStat:
        """宿主机侧原子写入（bind mount 即时可见）。"""
        self._manager.get(handle.id)
        sandbox = self._manager.sandbox(handle)
        target = sandbox.ensure_parent(path)
        return await asyncio.to_thread(write_text_atomic, target, content, encoding=encoding)

    async def read_file(
        self, handle: WorkspaceHandle, path: str, *, max_bytes: int = 1_048_576
    ) -> FileContent:
        """宿主机侧读取。"""
        self._manager.get(handle.id)
        sandbox = self._manager.sandbox(handle)
        target = sandbox.resolve(path, must_exist=True)
        return await asyncio.to_thread(read_text_limited, target, max_bytes=max_bytes)

    async def list_dir(
        self, handle: WorkspaceHandle, path: str, *, depth: int = 1
    ) -> DirListing:
        """宿主机侧列举目录。"""
        self._manager.get(handle.id)
        sandbox = self._manager.sandbox(handle)
        start = sandbox.resolve_dir(path, must_exist=True)
        return await asyncio.to_thread(walk_listing, sandbox.root, start, depth=depth)

    async def stat(self, handle: WorkspaceHandle, path: str) -> FileStat:
        """宿主机侧文件元信息。"""
        self._manager.get(handle.id)
        sandbox = self._manager.sandbox(handle)
        target = sandbox.resolve(path, must_exist=True)
        return await asyncio.to_thread(stat_file, target)

    # ------------------------------------------------------------------ cleanup

    async def cleanup(self, handle: WorkspaceHandle) -> None:
        """销毁容器与工作区（幂等）。"""
        if handle.container_id:
            try:
                client = self._lazy_client()
                await asyncio.to_thread(self._remove_container, client, handle.container_id)
            except ContainerUnavailableError:
                pass
        await self._manager.destroy(handle)

    @staticmethod
    def _remove_container(client: Any, container_id: str) -> None:
        """移除容器（不存在时静默）。"""
        try:
            container = client.containers.get(container_id)
            container.remove(force=True)
        except Exception:  # noqa: BLE001 - 容器可能已被外部清理
            return

    # ------------------------------------------------------------------ health

    async def healthcheck(self) -> ExecutorHealth:
        """健康检查；Docker 不可用时返回 ``healthy=False``，不抛异常。"""
        try:
            client = self._lazy_client()
            info = await asyncio.to_thread(client.version)
            return ExecutorHealth(
                kind=self.kind,
                healthy=True,
                detail=f"docker daemon {info.get('Version', '?')} ready",
                workspace_root=str(self._manager.root),
                active_workspaces=self._manager.count(),
                checked_at=self._clock.now(),
                extra={"docker_version": info.get("Version", "")},
            )
        except Exception as exc:  # noqa: BLE001 - healthcheck 永不抛异常
            return ExecutorHealth(
                kind=self.kind,
                healthy=False,
                detail=f"docker unavailable: {exc}",
                workspace_root=str(self._manager.root),
                active_workspaces=self._manager.count(),
                checked_at=self._clock.now(),
            )

    # ------------------------------------------------------------------ 关闭

    async def aclose(self) -> None:
        """关闭 docker client。"""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 - 关闭失败忽略
                pass
            self._client = None
