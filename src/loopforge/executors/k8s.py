"""``K8sJobExecutor`` —— Kubernetes Job 执行器（**v1.0 未启用，仅接口骨架**）。

这是全项目唯一被允许「不实现」的文件（``TASK_BREAKDOWN.md`` §5.1.1 明确声明）。
原因：K8s Job 执行需要完整的 RBAC / 网络策略 / 存储卷编排，属于生产部署级能力，
v1.0 的验收标准只要求「接口签名冻结 + 明确报错」，防止下游（WP4）在
``settings.executor.kind = "k8s"`` 时拿到一个静默降级的假实现。

行为约定
--------
* 任何方法调用都抛 :class:`~loopforge.executors.base.ExecutorNotEnabledError`
  （``LF-CORE-0005``），details 里写明 v1.0 未启用。
* :meth:`healthcheck` 例外：返回 ``healthy=False`` 而不是抛异常 —— 健康检查
  的契约是「永不抛异常」。
* 抽象方法签名与 :class:`~loopforge.executors.base.BaseExecutor` 完全一致，
  后续版本直接在此文件内补实现即可，下游代码零改动。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from loopforge.core.clock import Clock, get_clock
from loopforge.core.logging import get_logger
from loopforge.executors.base import (
    BaseExecutor,
    CommandResult,
    CommandSpec,
    DirListing,
    ExecutorHealth,
    ExecutorNotEnabledError,
    FileContent,
    FileStat,
    WorkspaceHandle,
    WorkspaceSpec,
)

__all__ = ["K8sJobExecutor"]

_log = get_logger(__name__)

#: v1.0 未启用时统一抛出的错误消息。
_NOT_ENABLED_MSG: str = (
    "K8sJobExecutor 在 v1.0 未启用：Kubernetes Job 执行需要 RBAC / 网络策略 / "
    "存储卷编排，请在 settings.executor.kind 中改用 local_subprocess 或 docker。"
)


class K8sJobExecutor(BaseExecutor):
    """Kubernetes Job 执行器（仅接口骨架）。

    Args:
        workspace_root: 工作区父目录（保留参数，v1.0 不使用）。
        clock: 时间源。
    """

    kind: ClassVar[str] = "k8s"
    enabled: ClassVar[bool] = False

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._workspace_root: Path = Path(workspace_root or "./data/workspaces-k8s")
        self._clock: Clock = clock or get_clock()

    def _not_enabled(self) -> None:
        """统一抛「未启用」错误。

        Raises:
            ExecutorNotEnabledError: 恒抛出。
        """
        raise ExecutorNotEnabledError(
            _NOT_ENABLED_MSG,
            details={"kind": self.kind, "reason": "not_enabled_v1"},
        )

    # -- 冻结签名（§11.4）—— v1.0 全部不启用 --------------------------------

    async def prepare(self, spec: WorkspaceSpec) -> WorkspaceHandle:
        """v1.0 未启用。"""
        self._not_enabled()
        raise AssertionError("unreachable")

    async def run(self, cmd: CommandSpec) -> CommandResult:
        """v1.0 未启用。"""
        self._not_enabled()
        raise AssertionError("unreachable")

    async def write_file(
        self, handle: WorkspaceHandle, path: str, content: str, *, encoding: str = "utf-8"
    ) -> FileStat:
        """v1.0 未启用。"""
        self._not_enabled()
        raise AssertionError("unreachable")

    async def read_file(
        self, handle: WorkspaceHandle, path: str, *, max_bytes: int = 1_048_576
    ) -> FileContent:
        """v1.0 未启用。"""
        self._not_enabled()
        raise AssertionError("unreachable")

    async def list_dir(
        self, handle: WorkspaceHandle, path: str, *, depth: int = 1
    ) -> DirListing:
        """v1.0 未启用。"""
        self._not_enabled()
        raise AssertionError("unreachable")

    async def cleanup(self, handle: WorkspaceHandle) -> None:
        """v1.0 未启用。"""
        self._not_enabled()
        raise AssertionError("unreachable")

    async def healthcheck(self) -> ExecutorHealth:
        """返回 ``healthy=False``（不抛异常）。"""
        return ExecutorHealth(
            kind=self.kind,
            healthy=False,
            detail="K8sJobExecutor 在 v1.0 未启用",
            workspace_root=str(self._workspace_root),
            active_workspaces=0,
            checked_at=self._clock.now(),
        )
