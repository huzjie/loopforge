"""``executors`` 包 —— 执行沙箱。

导出与工厂
----------
* **契约**：:class:`~loopforge.executors.base.BaseExecutor` 及全部 DTO
  （``CommandSpec`` / ``CommandResult`` / ``WorkspaceHandle`` ...）。
* **工厂**：:func:`create_executor` 按 ``settings.executor.kind`` 构造；
  未启用的 kind（如 ``k8s``）抛
  :class:`~loopforge.executors.base.ExecutorNotEnabledError`。
* **池**：:class:`ExecutorPool` 管理多个执行器的生命周期，避免每次工具调用都
  重新创建。

安全分层（自底向上）
-------------------
``path_sandbox``（路径钉死） → ``resource_limits``（资源限额） →
``command_policy`` + ``policy_guard``（命令策略） → ``workspace``（目录隔离） →
``local`` / ``docker``（执行实现）。
"""

from __future__ import annotations

from typing import Any, Literal

from loopforge.core.logging import get_logger
from loopforge.executors.base import (  # noqa: F401 - 再导出契约
    BaseExecutor,
    CommandResult,
    CommandSpec,
    ContainerUnavailableError,
    DirEntry,
    DirListing,
    ExecutorHealth,
    ExecutorNotEnabledError,
    FileContent,
    FileStat,
    WorkspaceHandle,
    WorkspaceSpec,
)
from loopforge.executors.command_policy import (  # noqa: F401
    CommandPolicy,
    CommandRule,
    CommandVerdict,
    DEFAULT_DENY_RULES,
)
from loopforge.executors.k8s import K8sJobExecutor  # noqa: F401
from loopforge.executors.local import LocalSubprocessExecutor  # noqa: F401
from loopforge.executors.docker import DockerExecutor  # noqa: F401
from loopforge.executors.path_sandbox import PathSandbox  # noqa: F401
from loopforge.executors.policy_guard import (  # noqa: F401
    CommandPolicyGuard,
    PolicyAction,
    PolicyVerdict,
)
from loopforge.executors.resource_limits import ResourceLimits  # noqa: F401
from loopforge.executors.workspace import WorkspaceManager  # noqa: F401

__all__ = [
    # 契约
    "BaseExecutor",
    "CommandSpec",
    "CommandResult",
    "WorkspaceSpec",
    "WorkspaceHandle",
    "FileStat",
    "FileContent",
    "DirEntry",
    "DirListing",
    "ExecutorHealth",
    # 异常
    "ContainerUnavailableError",
    "ExecutorNotEnabledError",
    # 实现
    "LocalSubprocessExecutor",
    "DockerExecutor",
    "K8sJobExecutor",
    "PathSandbox",
    "WorkspaceManager",
    "CommandPolicyGuard",
    "PolicyAction",
    "PolicyVerdict",
    "ResourceLimits",
    "CommandPolicy",
    "CommandRule",
    "CommandVerdict",
    # 工厂
    "create_executor",
    "ExecutorPool",
]

_log = get_logger(__name__)

ExecutorKind = Literal["local_subprocess", "docker", "k8s"]


def create_executor(kind: str | None = None, **kwargs: Any) -> BaseExecutor:
    """按种类构造执行器。

    Args:
        kind: ``local_subprocess`` / ``docker`` / ``k8s``；``None`` 取配置。
        **kwargs: 透传给执行器构造函数的参数。

    Returns:
        构造好的 :class:`BaseExecutor`。

    Raises:
        ExecutorNotEnabledError: ``k8s``（v1.0 未启用）或未知 kind。
    """
    from loopforge.core.config import get_settings

    selected = (kind or get_settings().executor.kind).strip().lower()
    if selected == "local_subprocess":
        return LocalSubprocessExecutor(**kwargs)
    if selected == "docker":
        return DockerExecutor(**kwargs)
    if selected == "k8s":
        return K8sJobExecutor(**kwargs)
    raise ExecutorNotEnabledError(
        f"未知执行器 kind：{selected!r}",
        details={"kind": selected, "available": ["local_subprocess", "docker", "k8s"]},
    )


class ExecutorPool:
    """执行器生命周期池。

    典型用法::

        pool = ExecutorPool()
        executor = await pool.acquire("local_subprocess")
        try:
            handle = await executor.prepare(WorkspaceSpec(run_id=...))
            ...
        finally:
            await pool.release(executor)

    Args:
        default_kind: 未指定 kind 时使用的执行器种类。
    """

    def __init__(self, *, default_kind: str = "local_subprocess") -> None:
        self._default_kind: str = default_kind
        self._pool: dict[str, BaseExecutor] = {}
        self._closed: bool = False

    async def acquire(self, kind: str | None = None) -> BaseExecutor:
        """取一个执行器（同 kind 复用单例）。

        Args:
            kind: 执行器种类；``None`` 用默认。

        Returns:
            :class:`BaseExecutor`。

        Raises:
            ExecutorNotEnabledError: 池已关闭或 kind 未启用。
        """
        if self._closed:
            raise ExecutorNotEnabledError(
                "ExecutorPool 已关闭，不能再 acquire",
                details={"reason": "pool_closed"},
            )
        key = (kind or self._default_kind).strip().lower()
        if key in self._pool:
            return self._pool[key]
        executor = create_executor(key)
        self._pool[key] = executor
        _log.info("executor_pool_acquired", kind=key)
        return executor

    async def release(self, executor: BaseExecutor) -> None:
        """归还执行器（当前实现为无操作，资源在 :meth:`close` 时统一释放）。"""
        _ = executor
        return None

    async def close(self) -> None:
        """关闭池中全部执行器。"""
        self._closed = True
        for key, executor in list(self._pool.items()):
            try:
                await executor.aclose()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不阻塞其他执行器
                _log.warning("executor_close_failed", kind=key, error=str(exc))
        self._pool.clear()

    def __len__(self) -> int:
        """池中执行器数量。"""
        return len(self._pool)
