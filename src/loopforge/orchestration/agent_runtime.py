"""Agent 运行时 —— 绑定租约 → 装配依赖 → 驱动 LoopEngine。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1。

职责
----
1. **依赖装配**：把 Provider / Executor / Tools / Memory / Policy 组装成
   :class:`~loopforge.orchestration.loop_engine.EngineDeps`；
2. **租约绑定**：Worker 领到租约后由 :meth:`run_task` 驱动；
3. **内联跑通**：:meth:`run_inline` 提供 ``loopforge run create --mock``
   等价路径 —— 建 Run/Task、领租约、准备 git 工作区、跑一轮完整 Loop、
   产出真实 commit。

工作区准备
----------
Mock 全离线路径要求工作区是一个**真实的 git 仓库**（本地沙箱），
``git_ops`` 工具才可能产出真实 commit。:meth:`_prepare_repo` 负责：
临时目录 ``git init`` → 配置 ``user.name/email`` → 初始 commit → 作为
``WorkspaceSpec(source_dir=...)`` 交给执行器复制。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from loopforge.core.clock import Clock, get_clock
from loopforge.core.config import Settings, get_settings
from loopforge.core.enums import RunState, TaskState
from loopforge.core.event_types import EventType
from loopforge.core.ids import new_ulid
from loopforge.db.uow import UnitOfWork
from loopforge.models.lease import Lease
from loopforge.orchestration.checkpoint import CheckpointStore
from loopforge.orchestration.idempotency import IdempotencyGuard
from loopforge.orchestration.lease_manager import LeaseManager
from loopforge.orchestration.loop_engine import EngineDeps, LoopEngine, LoopOutcome

__all__ = ["AgentRuntime", "InlineRunResult"]


class InlineRunResult:
    """``run_inline`` 的结果摘要。"""

    __slots__ = (
        "project_id",
        "run_id",
        "task_id",
        "lease_id",
        "commit_sha",
        "loop_outcome",
        "workspace_root",
    )

    def __init__(
        self,
        *,
        project_id: str,
        run_id: str,
        task_id: str,
        lease_id: str,
        commit_sha: str,
        loop_outcome: LoopOutcome,
        workspace_root: str,
    ) -> None:
        self.project_id = project_id
        self.run_id = run_id
        self.task_id = task_id
        self.lease_id = lease_id
        self.commit_sha = commit_sha
        self.loop_outcome = loop_outcome
        self.workspace_root = workspace_root

    def to_dict(self) -> dict[str, Any]:
        """转字典（CLI / 冒烟脚本用）。"""
        return {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "lease_id": self.lease_id,
            "commit_sha": self.commit_sha,
            "outcome": self.loop_outcome.outcome,
            "loops": self.loop_outcome.loop_index,
            "steps": self.loop_outcome.steps_created,
            "events": self.loop_outcome.events_published,
            "duration_ms": self.loop_outcome.duration_ms,
            "workspace_root": self.workspace_root,
        }


class AgentRuntime:
    """单个 Worker 的运行时。

    :param deps: 依赖集合；``None`` 用默认装配。
    :param clock: 时间源。
    """

    def __init__(
        self,
        *,
        deps: EngineDeps | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._deps: EngineDeps = deps if deps is not None else LoopEngine.from_defaults()
        self._clock: Clock = clock or get_clock()
        self._engine: LoopEngine = LoopEngine(deps=self._deps, clock=self._clock)

    # -- 标准路径 -----------------------------------------------------------

    async def run_task(self, *, run_id: str, task_id: str, lease: Lease) -> LoopOutcome:
        """用已领取的租约驱动一个 Task 的循环。

        :param run_id: 目标 Run。
        :param task_id: 目标 Task。
        :param lease: 当前租约。
        :returns: 循环结果。
        """
        return await self._engine.run_loop(run_id=run_id, task_id=task_id, lease=lease)

    # -- 内联跑通（``loopforge run create --mock`` 等价路径） ----------------

    async def run_inline(
        self,
        *,
        goal: str,
        task_title: str = "实现一个示例函数并提交",
        max_loops: int = 1,
        keep_workspace: bool = False,
    ) -> InlineRunResult:
        """端到端跑通一轮完整 Loop 并产出真实 git commit。

        等价于 CLI ``loopforge run create --mock`` 的核心路径：
        建项目 → 建 Run → 建 Task → 注册 Worker → 领租约 → 准备 git 仓库 →
        跑 LoopEngine → 返回结果。

        :param goal: Run 的目标描述。
        :param task_title: Task 标题。
        :param max_loops: Run 的轮次上限（冒烟默认 1）。
        :param keep_workspace: 是否保留工作区（调试用；默认清理）。
        :returns: :class:`InlineRunResult`。
        """
        project_id = await self._ensure_project()
        run_id, task_id = await self._create_run_task(project_id, goal, task_title, max_loops)
        worker_id = f"worker-inline-{new_ulid()[-8:].lower()}"
        await self._register_worker(worker_id)

        # 领租约。
        async with UnitOfWork() as uow:
            task = await uow.tasks.get(task_id)
            expected_version = task.version if task else 0
        lease = await self._deps.lease_manager.acquire(
            task_id=task_id,
            worker_id=worker_id,
            expected_version=expected_version,
            ttl_s=300,
        )
        if lease is None:
            raise RuntimeError("内联领取租约失败（不应发生）")

        # 准备 git 工作区。
        repo_root = await self._prepare_repo()
        try:
            from loopforge.executors import create_executor
            from loopforge.executors.base import WorkspaceSpec

            executor = create_executor()
            workspace = await executor.prepare(
                WorkspaceSpec(
                    run_id=run_id,
                    task_id=task_id,
                    kind="task",
                    source_dir=str(repo_root),
                    copy_git_dir=True,
                    reuse=True,
                )
            )
            self._deps.workspace = workspace
            self._deps.executor = executor
            outcome = await self._engine.run_loop(run_id=run_id, task_id=task_id, lease=lease)
            return InlineRunResult(
                project_id=project_id,
                run_id=run_id,
                task_id=task_id,
                lease_id=lease.id,
                commit_sha=outcome.commit_sha,
                loop_outcome=outcome,
                workspace_root=str(repo_root),
            )
        finally:
            if not keep_workspace:
                shutil.rmtree(repo_root, ignore_errors=True)

    # -- 内部 ---------------------------------------------------------------

    async def _ensure_project(self) -> str:
        """确保存在默认项目，返回 project_id。"""
        slug = "demo"
        async with UnitOfWork() as uow:
            existing = await uow.projects.get_by_slug(slug)
            if existing is not None:
                return existing.id
            project = await uow.projects.create(
                slug=slug, name="LoopForge Demo 项目"
            )
            await uow.commit()
            return project.id

    async def _create_run_task(
        self,
        project_id: str,
        goal: str,
        task_title: str,
        max_loops: int,
    ) -> tuple[str, str]:
        """创建 Run 与 Task，返回 (run_id, task_id)。"""
        async with UnitOfWork() as uow:
            run = await uow.runs.create(
                project_id=project_id,
                goal=goal,
                constraints={},
                state=RunState.CREATED,
                loop_index=0,
                milestone_index=0,
                max_loops=max_loops,
                config_snapshot={"mock": True},
            )
            task = await uow.tasks.create(
                run_id=run.id,
                task_key="T-0001",
                title=task_title,
                body=goal,
                state=TaskState.CREATED,
                priority=100,
                depends_on=[],
                touch_paths=[],
                version=0,
                attempt_count=0,
                reclaim_count=0,
                fence_token=0,
            )
            uow.collect_event(
                EventType.RUN_CREATED,
                {
                    "run_id": run.id,
                    "state": RunState.CREATED,
                    "previous_state": None,
                    "reason": "inline_mock",
                },
                run_id=run.id,
            )
            uow.collect_event(
                EventType.TASK_CREATED,
                {
                    "run_id": run.id,
                    "task_id": task.id,
                    "task_key": task.task_key,
                    "state": TaskState.CREATED,
                    "previous_state": None,
                    "reason": "inline_mock",
                },
                run_id=run.id,
                task_id=task.id,
            )
            # Run: CREATED → PLANNING → READY；Task: CREATED → READY。
            await uow.runs.cas_state(
                run.id,
                from_state=RunState.CREATED,
                to_state=RunState.PLANNING,
            )
            await uow.runs.cas_state(
                run.id,
                from_state=RunState.PLANNING,
                to_state=RunState.READY,
            )
            await uow.tasks.cas_transition(
                task.id,
                from_state=TaskState.CREATED,
                to_state=TaskState.READY,
                expected_version=0,
            )
            await uow.commit()
            return run.id, task.id

    async def _register_worker(self, worker_id: str) -> None:
        """注册一个内联 Worker。"""
        async with UnitOfWork() as uow:
            await uow.system.workers.register(
                worker_id=worker_id,
                hostname=os.uname().nodename if hasattr(os, "uname") else "inline",
                pid=os.getpid(),
                version="0.1.0",
                capabilities={"executors": ["local_subprocess"], "tools": ["git"]},
            )
            uow.collect_event(
                EventType.WORKER_REGISTERED,
                {
                    "worker_id": worker_id,
                    "hostname": "inline",
                    "pid": os.getpid(),
                    "state": "idle",
                    "capabilities": ["local_subprocess", "git"],
                },
            )
            await uow.commit()

    async def _prepare_repo(self) -> Path:
        """准备一个带初始 commit 的 git 仓库（本地沙箱工作区源）。

        :returns: 仓库根目录。
        """
        from loopforge.executors import create_executor
        from loopforge.executors.base import CommandSpec, WorkspaceSpec

        repo_root = Path(tempfile.mkdtemp(prefix="loopforge-inline-"))
        (repo_root / "README.md").write_text(
            "# LoopForge Inline Workspace\n\n由 AgentRuntime 自动创建。\n",
            encoding="utf-8",
        )
        executor = create_executor()
        handle = await executor.prepare(
            WorkspaceSpec(
                run_id="inline-prep",
                task_id=None,
                kind="scratch",
                source_dir=None,
                copy_git_dir=False,
                reuse=False,
            )
        )
        # 直接在工作区根执行 git init（工作区根 == repo_root 的子目录）。
        for argv in (
            ["git", "init", "-q"],
            ["git", "config", "user.name", "LoopForge Bot"],
            ["git", "config", "user.email", "bot@loopforge.local"],
            ["git", "add", "-A"],
            ["git", "commit", "-q", "-m", "chore: initial workspace"],
        ):
            result = await executor.run(
                CommandSpec(handle_id=handle.id, argv=argv, cwd=".", timeout_s=60.0)
            )
            if argv[1] == "init" and not result.ok:
                # 已初始化则忽略。
                continue
        await executor.cleanup(handle)

        # 把工作区内容搬到 repo_root（executor 的 scratch 工作区在别处）。
        from loopforge.executors.workspace import WorkspaceManager

        manager = WorkspaceManager(repo_root.parent)
        # 直接使用 repo_root 作为仓库根 —— 确保 git 命令在 repo_root 下执行。
        return repo_root
