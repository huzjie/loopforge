"""``BaseExecutor`` 抽象基类与执行层数据契约（**签名已冻结**）。

冻结来源：``docs/TASK_BREAKDOWN.md`` §11.4 / ``docs/ARCHITECTURE.md`` §7.2。

契约要点
--------
1. **``argv: list[str]``，全仓禁止 ``shell=True``**。命令注入的根因是把字符串
   交给 shell 解释，argv 数组从源头消灭这一类攻击面。
2. 所有路径参数都是**相对工作区根**的相对路径；绝对路径与 ``..`` 由
   :mod:`loopforge.executors.path_sandbox` 统一拦截。
3. 输出**必须截断且可追溯**：超过 ``max_output_bytes`` 时置 ``truncated=True``，
   禁止把 10MB 日志塞进 LLM 上下文。
4. 时间一律走 :class:`loopforge.core.clock.Clock`，禁止 ``datetime.now()``。

命名兼容
--------
下游（WP4/WP6）文档里出现过 ``run_command`` / ``prepare_workspace`` /
``health_check`` 三个别名。为避免下游因命名分歧而阻塞，本类提供**具体的**
同义方法转发到冻结签名，抽象方法仍然只有 §11.4 的七个。
"""

from __future__ import annotations

import abc
from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from loopforge.constants import DEFAULT_ENCODING
from loopforge.core.error_codes import ErrorCode
from loopforge.core.errors import ConfigError, ExecutorError, LoopForgeError

__all__ = [
    # 错误
    "PathEscapeError",
    "CommandTimeoutError",
    "MemoryLimitError",
    "ContainerUnavailableError",
    "OutputLimitError",
    "WorkspaceNotReadyError",
    "NonZeroExitError",
    "InvalidCommandError",
    "ExecutorNotEnabledError",
    # 数据契约
    "WorkspaceSpec",
    "WorkspaceHandle",
    "CommandSpec",
    "CommandResult",
    "FileStat",
    "FileContent",
    "DirEntry",
    "DirListing",
    "ExecutorHealth",
    # 抽象
    "BaseExecutor",
    "DEFAULT_MAX_READ_BYTES",
]

#: ``read_file`` 的默认字节上限（与 §11.4 冻结签名一致）。
DEFAULT_MAX_READ_BYTES: int = 1_048_576


# ---------------------------------------------------------------------------
# 执行层异常（EXECUTOR 域 0800-0899）
# ---------------------------------------------------------------------------


class PathEscapeError(ExecutorError):
    """路径逃逸出工作区（``LF-EXECUTOR-0801``）。"""

    default_code = ErrorCode.EXECUTOR_PATH_ESCAPE


class CommandTimeoutError(ExecutorError):
    """命令执行超时（``LF-EXECUTOR-0802``）。"""

    default_code = ErrorCode.EXECUTOR_TIMEOUT


class MemoryLimitError(ExecutorError):
    """命令执行内存超限（``LF-EXECUTOR-0803``）。"""

    default_code = ErrorCode.EXECUTOR_OOM


class ContainerUnavailableError(ExecutorError):
    """容器运行时不可用（``LF-EXECUTOR-0804``）。

    Docker SDK 缺失、daemon 未启动、镜像拉取失败统一走这个码 ——
    **绝不允许直接崩栈**，下游据此降级到 ``LocalSubprocessExecutor``。
    """

    default_code = ErrorCode.EXECUTOR_CONTAINER_UNAVAILABLE


class OutputLimitError(ExecutorError):
    """命令输出超过上限且策略要求硬失败（``LF-EXECUTOR-0805``）。"""

    default_code = ErrorCode.EXECUTOR_OUTPUT_LIMIT


class WorkspaceNotReadyError(ExecutorError):
    """工作区句柄未知或目录已被清理（``LF-EXECUTOR-0806``）。"""

    default_code = ErrorCode.EXECUTOR_WORKSPACE_NOT_READY


class NonZeroExitError(ExecutorError):
    """命令以非零状态退出（``LF-EXECUTOR-0807``）。"""

    default_code = ErrorCode.EXECUTOR_NONZERO_EXIT


class InvalidCommandError(LoopForgeError):
    """命令本身非法（空 argv / 含 NUL / 参数类型错误）。"""

    default_code = ErrorCode.CORE_INVALID_ARGUMENT


class ExecutorNotEnabledError(ConfigError):
    """该 Executor 在当前版本未启用（``LF-CORE-0005``）。"""

    default_code = ErrorCode.CORE_FEATURE_DISABLED


# ---------------------------------------------------------------------------
# 数据契约
# ---------------------------------------------------------------------------


class _ExecModel(BaseModel):
    """执行层 DTO 基类。

    与 :class:`loopforge.schemas.common.LFModel` 的差别：这里**不开启**
    ``validate_assignment``，因为 ``CommandResult`` 这类对象会在流式读取过程中
    被高频改写，逐字段再校验纯属浪费。
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=False, populate_by_name=True)


class WorkspaceSpec(_ExecModel):
    """工作区申请单 —— :meth:`BaseExecutor.prepare` 的入参。"""

    run_id: str = Field(min_length=1, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    kind: Literal["run", "task", "scratch"] = "run"
    source_dir: str | None = Field(
        default=None, description="初始化时从该目录复制内容（如已 clone 的仓库）"
    )
    copy_git_dir: bool = Field(default=True, description="复制 source_dir 时是否带上 .git")
    quota_bytes: int | None = Field(default=None, ge=0, description="磁盘配额，None 表示不限")
    env: dict[str, str] = Field(default_factory=dict, description="工作区级默认环境变量")
    reuse: bool = Field(default=True, description="同 (run_id, task_id, kind) 已存在时复用")
    image: str | None = Field(default=None, description="容器执行器使用的镜像，None 用配置默认")

    @property
    def slug(self) -> str:
        """工作区目录名（稳定、可读、文件系统安全）。"""
        parts = [self.kind, self.run_id]
        if self.task_id:
            parts.append(self.task_id)
        return "-".join(parts)


class WorkspaceHandle(_ExecModel):
    """工作区句柄 —— 后续所有文件与命令操作的坐标系原点。"""

    id: str = Field(min_length=1, max_length=64)
    root: str = Field(min_length=1, description="工作区根目录的绝对路径（宿主机视角）")
    run_id: str = Field(min_length=1, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    kind: Literal["run", "task", "scratch"] = "run"
    executor_kind: str = Field(default="local_subprocess")
    container_id: str | None = Field(default=None, description="DockerExecutor 才有")
    quota_bytes: int | None = Field(default=None, ge=0)
    env: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = Field(default=None)

    def __hash__(self) -> int:
        return hash(self.id)


class CommandSpec(_ExecModel):
    """命令执行申请单（**字段冻结**，见 ARCHITECTURE §7.2）。"""

    handle_id: str = Field(min_length=1, max_length=64)
    argv: list[str] = Field(min_length=1, description="禁止 shell=True，一律 argv 数组")
    cwd: str = Field(default=".", description="相对 workspace 根")
    env: dict[str, str] = Field(default_factory=dict, description="与白名单求交集后生效")
    timeout_s: float = Field(default=300.0, gt=0)
    max_output_bytes: int = Field(default=DEFAULT_MAX_READ_BYTES, ge=0)
    network: Literal["none", "restricted"] = "none"
    stdin: str | None = Field(default=None)

    def display(self) -> str:
        """给日志与审计用的单行展示（**不可回灌给 shell 执行**）。"""
        return " ".join(self.argv)


class CommandResult(_ExecModel):
    """命令执行结果（**字段冻结**）。"""

    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    truncated: bool = False
    timed_out: bool = False
    oom_killed: bool = False

    @property
    def ok(self) -> bool:
        """退出码为 0 且未超时即视为成功。"""
        return self.exit_code == 0 and not self.timed_out

    def combined_output(self, *, limit: int = 8000) -> str:
        """stdout + stderr 的合并摘要，用于喂给 LLM 时的短上下文。"""
        merged = self.stdout
        if self.stderr:
            merged = f"{merged}\n--- stderr ---\n{self.stderr}" if merged else self.stderr
        if len(merged) <= limit:
            return merged
        head = merged[: limit // 2]
        tail = merged[-(limit // 2) :]
        return f"{head}\n...[已截断 {len(merged) - limit} 字符]...\n{tail}"


class FileStat(_ExecModel):
    """文件写入 / 探测结果。"""

    path: str
    size_bytes: int = Field(default=0, ge=0)
    sha256: str = ""
    is_dir: bool = False
    exists: bool = True
    encoding: str = DEFAULT_ENCODING
    mtime: datetime | None = None
    created: bool = Field(default=False, description="本次操作是否创建了新文件")


class FileContent(_ExecModel):
    """文件读取结果。"""

    path: str
    content: str = ""
    encoding: str = DEFAULT_ENCODING
    size_bytes: int = Field(default=0, ge=0)
    sha256: str = ""
    truncated: bool = False
    line_count: int = Field(default=0, ge=0)
    is_binary: bool = False


class DirEntry(_ExecModel):
    """目录条目。"""

    name: str
    path: str = Field(description="相对工作区根，使用 POSIX 分隔符")
    is_dir: bool = False
    size_bytes: int = Field(default=0, ge=0)
    depth: int = Field(default=1, ge=0)


class DirListing(_ExecModel):
    """目录列举结果。"""

    path: str
    entries: list[DirEntry] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    truncated: bool = False

    def as_tree(self) -> str:
        """渲染成缩进树，直接可读、可喂给模型。"""
        lines: list[str] = []
        for entry in self.entries:
            indent = "  " * max(entry.depth - 1, 0)
            suffix = "/" if entry.is_dir else f"  ({entry.size_bytes}B)"
            lines.append(f"{indent}{entry.name}{suffix}")
        if self.truncated:
            lines.append("...[条目已截断]")
        return "\n".join(lines)


class ExecutorHealth(_ExecModel):
    """执行器健康状态。"""

    kind: str
    healthy: bool = True
    detail: str = ""
    workspace_root: str = ""
    active_workspaces: int = Field(default=0, ge=0)
    checked_at: datetime | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class BaseExecutor(abc.ABC):
    """执行沙箱抽象基类。

    子类必须声明 :attr:`kind` 并实现七个抽象方法。**不要在子类里加新的公共
    抽象方法** —— 那会让下游同时依赖两套契约，跨包升级立刻失控。
    """

    #: 执行器种类名，与 ``settings.executor.kind`` 对应。
    kind: ClassVar[str] = "base"

    #: 该执行器是否在当前版本启用（``False`` 时 factory 拒绝构造）。
    enabled: ClassVar[bool] = True

    # -- 冻结签名（§11.4） --------------------------------------------------

    @abc.abstractmethod
    async def prepare(self, spec: WorkspaceSpec) -> WorkspaceHandle:
        """准备工作区并返回句柄。

        Args:
            spec: 工作区申请单。

        Returns:
            可用于后续所有操作的 :class:`WorkspaceHandle`。

        Raises:
            WorkspaceNotReadyError: 目录创建失败或配额不足。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def run(self, cmd: CommandSpec) -> CommandResult:
        """在工作区内执行命令。

        Args:
            cmd: 命令申请单，``argv`` 数组形式。

        Returns:
            :class:`CommandResult`；**非零退出码不抛异常**，由调用方判断。

        Raises:
            PathEscapeError: ``cwd`` 逃逸出工作区。
            WorkspaceNotReadyError: ``handle_id`` 未知。
            loopforge.core.errors.PolicyDeniedError: 命中命令黑名单。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def write_file(
        self, handle: WorkspaceHandle, path: str, content: str, *, encoding: str = "utf-8"
    ) -> FileStat:
        """原子写入文本文件（临时文件 + rename）。

        Args:
            handle: 工作区句柄。
            path: 相对工作区根的路径。
            content: 文本内容。
            encoding: 文本编码。

        Returns:
            含 ``sha256`` 的 :class:`FileStat`，供 ``probe()`` 比对。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def read_file(
        self, handle: WorkspaceHandle, path: str, *, max_bytes: int = DEFAULT_MAX_READ_BYTES
    ) -> FileContent:
        """读取文本文件。

        Args:
            handle: 工作区句柄。
            path: 相对工作区根的路径。
            max_bytes: 最多读取字节数，超出置 ``truncated=True``。

        Returns:
            :class:`FileContent`。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def list_dir(
        self, handle: WorkspaceHandle, path: str, *, depth: int = 1
    ) -> DirListing:
        """列举目录。

        Args:
            handle: 工作区句柄。
            path: 相对工作区根的路径。
            depth: 递归深度，``1`` 表示只列当前层。

        Returns:
            :class:`DirListing`。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def cleanup(self, handle: WorkspaceHandle) -> None:
        """销毁工作区并释放资源。

        Args:
            handle: 工作区句柄。**重复调用必须幂等**。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def healthcheck(self) -> ExecutorHealth:
        """健康检查。**任何情况下都不得抛异常**，用 ``healthy=False`` 表达故障。"""
        raise NotImplementedError

    # -- 具体的命名别名（下游文档里出现过的另一套叫法） ----------------------

    async def run_command(self, cmd: CommandSpec) -> CommandResult:
        """:meth:`run` 的别名，便于下游按语义化命名调用。"""
        return await self.run(cmd)

    async def prepare_workspace(self, spec: WorkspaceSpec) -> WorkspaceHandle:
        """:meth:`prepare` 的别名。"""
        return await self.prepare(spec)

    async def health_check(self) -> ExecutorHealth:
        """:meth:`healthcheck` 的别名。"""
        return await self.healthcheck()

    # -- 便捷组合方法（具体实现，非抽象） -----------------------------------

    async def run_checked(self, cmd: CommandSpec) -> CommandResult:
        """执行命令并在失败时抛出异常。

        Args:
            cmd: 命令申请单。

        Returns:
            成功的 :class:`CommandResult`。

        Raises:
            CommandTimeoutError: 命令超时。
            NonZeroExitError: 退出码非零。
        """
        result = await self.run(cmd)
        if result.timed_out:
            raise CommandTimeoutError(
                f"命令超时：{cmd.display()}",
                details={"argv": cmd.argv, "timeout_s": cmd.timeout_s},
            )
        if result.exit_code != 0:
            raise NonZeroExitError(
                f"命令失败（exit={result.exit_code}）：{cmd.display()}",
                details={
                    "argv": cmd.argv,
                    "exit_code": result.exit_code,
                    "stderr": result.stderr[-2000:],
                },
            )
        return result

    async def aclose(self) -> None:
        """释放执行器自身持有的长生命周期资源。默认无操作。"""
        return None

    async def __aenter__(self) -> BaseExecutor:
        """支持 ``async with``。"""
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """退出时释放资源。"""
        await self.aclose()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(kind={self.kind!r})"
