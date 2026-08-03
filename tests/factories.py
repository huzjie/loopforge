"""测试数据工厂 —— 用最少的入参造出合法的 ORM 行。

约定
----
* 每个 ``make_*`` 都是 ``async`` 函数，第一个位置参数是
  :class:`~loopforge.db.uow.UnitOfWork`，其余全部走关键字并带默认值。
* 所有工厂只 ``flush``、不 ``commit`` —— 事务边界由调用方（或 ``uow`` 夹具）掌握。
* 唯一性字段（``slug`` / ``task_key`` / ``username`` ...）自动带序号后缀，
  同一个测试里连续调用不会撞唯一索引。
* **不使用 ``datetime.now()``** —— 时间一律来自
  :func:`loopforge.core.clock.get_clock`，与仓储层保持同一个时钟。

用法::

    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    task = await make_task(uow, run_id=run.id)
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from loopforge.core.clock import get_clock
from loopforge.core.enums import (
    BudgetScope,
    MemoryLayer,
    RoleName,
    RunState,
    TaskState,
)
from loopforge.core.ids import new_ulid
from loopforge.db.uow import UnitOfWork
from loopforge.models.agent_role import AgentRole
from loopforge.models.auth import ApiKey, User
from loopforge.models.budget import Budget
from loopforge.models.memory import MemoryItem
from loopforge.models.project import Project, Repo
from loopforge.models.run import Run
from loopforge.models.task import Task
from loopforge.models.worker import Worker

__all__ = [
    "next_suffix",
    "reset_counter",
    "make_project",
    "make_repo",
    "make_run",
    "make_task",
    "make_tasks",
    "make_worker",
    "make_memory_item",
    "make_budget",
    "make_user",
    "make_api_key",
    "make_agent_role",
]

#: 进程内单调计数器，保证工厂产出的唯一键不重复。
_counter = itertools.count(1)


def next_suffix() -> int:
    """取下一个唯一后缀。

    Returns:
        单调递增的正整数。
    """
    return next(_counter)


def reset_counter() -> None:
    """重置计数器（仅在需要确定性命名的测试里调用）。"""
    global _counter  # noqa: PLW0603 —— 测试工具，允许重置模块级计数器
    _counter = itertools.count(1)


def _now() -> datetime:
    """当前时刻（走全局 Clock，可被 FrozenClock 接管）。"""
    return get_clock().now()


async def make_project(
    uow: UnitOfWork,
    *,
    slug: str | None = None,
    name: str | None = None,
    description: str = "工厂生成的测试项目",
    settings: dict[str, Any] | None = None,
) -> Project:
    """创建一个 Project。

    Args:
        uow: 活跃事务。
        slug: URL 标识，默认自动生成唯一值。
        name: 展示名。
        description: 项目描述。
        settings: 项目级配置覆盖。

    Returns:
        已 flush 的 :class:`Project`。
    """
    index = next_suffix()
    return await uow.projects.create(
        slug=slug or f"proj-{index}",
        name=name or f"Project {index}",
        description=description,
        settings=dict(settings) if settings else {},
    )


async def make_repo(
    uow: UnitOfWork,
    *,
    project_id: str,
    provider: str = "github",
    remote_url: str | None = None,
    default_branch: str = "main",
    webhook_enabled: bool = False,
) -> Repo:
    """创建一个 Repo。

    Args:
        uow: 活跃事务。
        project_id: 所属项目。
        provider: 代码托管平台。
        remote_url: 远端地址，默认自动生成。
        default_branch: 默认分支。
        webhook_enabled: 是否开启 webhook。

    Returns:
        已 flush 的 :class:`Repo`。
    """
    index = next_suffix()
    return await uow.repos.create(
        project_id=project_id,
        provider=provider,
        remote_url=remote_url or f"https://github.com/loopforge/demo-{index}.git",
        default_branch=default_branch,
        labels_config={},
        webhook_enabled=webhook_enabled,
    )


async def make_run(
    uow: UnitOfWork,
    *,
    project_id: str,
    repo_id: str | None = None,
    goal: str = "把测试跑绿",
    state: RunState = RunState.CREATED,
    max_loops: int = 100,
    loop_index: int = 0,
    constraints: dict[str, Any] | None = None,
) -> Run:
    """创建一个 Run。

    Args:
        uow: 活跃事务。
        project_id: 所属项目。
        repo_id: 关联仓库（可空）。
        goal: 自然语言目标。
        state: 初始状态。
        max_loops: 循环上限。
        loop_index: 起始循环序号。
        constraints: 约束字典。

    Returns:
        已 flush 的 :class:`Run`。
    """
    return await uow.runs.create(
        project_id=project_id,
        repo_id=repo_id,
        goal=goal,
        state=state,
        max_loops=max_loops,
        loop_index=loop_index,
        constraints=dict(constraints) if constraints else {},
        config_snapshot={},
    )


async def make_task(
    uow: UnitOfWork,
    *,
    run_id: str,
    task_key: str | None = None,
    title: str | None = None,
    state: TaskState = TaskState.READY,
    priority: int = 50,
    depends_on: list[str] | None = None,
    touch_paths: list[str] | None = None,
    repo_id: str | None = None,
) -> Task:
    """创建一个 Task。

    Args:
        uow: 活跃事务。
        run_id: 所属 Run。
        task_key: Run 内唯一键，默认自动生成。
        title: 任务标题。
        state: 初始状态，默认 ``READY``（可直接被领取）。
        priority: 优先级，数字越小越优先。
        depends_on: 依赖的 task_key 列表。
        touch_paths: 触碰的文件路径（冲突检测用）。
        repo_id: 关联仓库。

    Returns:
        已 flush 的 :class:`Task`。
    """
    index = next_suffix()
    return await uow.tasks.create(
        run_id=run_id,
        repo_id=repo_id,
        task_key=task_key or f"T-{index:04d}",
        title=title or f"任务 {index}",
        body="",
        state=state,
        priority=priority,
        depends_on=list(depends_on) if depends_on else [],
        touch_paths=list(touch_paths) if touch_paths else [],
        result={},
    )


async def make_tasks(
    uow: UnitOfWork,
    *,
    run_id: str,
    count: int,
    state: TaskState = TaskState.READY,
) -> list[Task]:
    """批量创建 Task。

    Args:
        uow: 活跃事务。
        run_id: 所属 Run。
        count: 数量，必须为正。
        state: 统一的初始状态。

    Returns:
        Task 列表，顺序与创建顺序一致。

    Raises:
        ValueError: ``count`` 非正。
    """
    if count < 1:
        raise ValueError(f"count 必须 >= 1，收到 {count}")
    return [await make_task(uow, run_id=run_id, state=state) for _ in range(count)]


async def make_worker(
    uow: UnitOfWork,
    *,
    worker_id: str | None = None,
    hostname: str = "test-host",
    pid: int = 4242,
    version: str = "0.1.0",
    capabilities: dict[str, Any] | None = None,
) -> Worker:
    """注册一个 Worker。

    Args:
        uow: 活跃事务。
        worker_id: Worker 主键，默认新 ULID。
        hostname: 主机名。
        pid: 进程号。
        version: LoopForge 版本号。
        capabilities: 能力声明字典。

    Returns:
        已 flush 的 :class:`Worker`。
    """
    return await uow.workers.register(
        worker_id=worker_id or new_ulid(),
        hostname=hostname,
        pid=pid,
        version=version,
        capabilities=dict(capabilities) if capabilities else {"executor": "local"},
    )


async def make_memory_item(
    uow: UnitOfWork,
    *,
    run_id: str,
    content: str = "记忆内容",
    layer: MemoryLayer = MemoryLayer.WORKING,
    tokens: int = 16,
    salience: float = 0.5,
) -> MemoryItem:
    """写入一条记忆。

    Args:
        uow: 活跃事务。
        run_id: 所属 Run。
        content: 记忆正文。
        layer: 记忆层。
        tokens: token 估算值。
        salience: 显著性 ``[0, 1]``。

    Returns:
        已 flush 的 :class:`MemoryItem`。
    """
    return await uow.memory.add_item(
        run_id=run_id,
        content=content,
        layer=layer,
        tokens=tokens,
        salience=salience,
    )


async def make_budget(
    uow: UnitOfWork,
    *,
    scope: BudgetScope = BudgetScope.RUN,
    scope_ref: str | None = None,
    window: str = "run",
    limit_usd: Decimal | float = Decimal("5.0"),
) -> Budget:
    """创建一个预算。

    Args:
        uow: 活跃事务。
        scope: 预算作用域。
        scope_ref: 作用域引用（Run ID / 项目 ID / ``"global"``）。
        window: 统计窗口。
        limit_usd: 上限金额。

    Returns:
        已 flush 的 :class:`Budget`。
    """
    return await uow.budgets.create(
        scope=scope,
        scope_ref=scope_ref or f"ref-{next_suffix()}",
        window=window,
        limit_usd=Decimal(str(limit_usd)),
        warn_at=0.7,
        throttle_at=0.9,
        halt_at=1.0,
        window_start=_now(),
        active=True,
    )


async def make_user(
    uow: UnitOfWork,
    *,
    username: str | None = None,
    role: str = "operator",
    hashed_password: str = "argon2id$dummy$hash",
) -> User:
    """创建一个用户。

    Args:
        uow: 活跃事务。
        username: 用户名，默认自动生成唯一值。
        role: 角色。
        hashed_password: 已哈希的口令（**测试里不放明文**）。

    Returns:
        已 flush 的 :class:`User`。
    """
    return await uow.users.create_user(
        username=username or f"user{next_suffix()}",
        hashed_password=hashed_password,
        role=role,
    )


async def make_api_key(
    uow: UnitOfWork,
    *,
    user_id: str,
    name: str = "ci-key",
    scopes: list[str] | None = None,
    ttl_days: int = 30,
) -> ApiKey:
    """签发一把 API Key（只存哈希）。

    Args:
        uow: 活跃事务。
        user_id: 归属用户。
        name: 密钥名称。
        scopes: 权限范围列表。
        ttl_days: 有效期天数。

    Returns:
        已 flush 的 :class:`ApiKey`。
    """
    index = next_suffix()
    return await uow.api_keys.issue(
        user_id=user_id,
        name=f"{name}-{index}",
        prefix=f"lf{index:06d}",
        hashed_secret=f"sha256${index:064d}",
        scopes=list(scopes) if scopes else ["run:read"],
        ttl_days=ttl_days,
    )


async def make_agent_role(
    uow: UnitOfWork,
    *,
    name: RoleName = RoleName.PLANNER,
    provider_config_id: str | None = None,
    model: str = "mock-large",
    temperature: float = 0.2,
    max_tokens: int = 4096,
    allowed_tools: list[str] | None = None,
) -> AgentRole:
    """写入一个角色绑定。

    Args:
        uow: 活跃事务。
        name: 角色名。
        provider_config_id: 绑定的 Provider 配置行 ID（可空）。
        model: 模型名。
        temperature: 采样温度。
        max_tokens: 最大输出 token。
        allowed_tools: 允许调用的工具白名单。

    Returns:
        已 flush 的 :class:`AgentRole`。
    """
    return await uow.agent_roles.upsert(
        name,
        provider_config_id=provider_config_id,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        allowed_tools=list(allowed_tools) if allowed_tools else [],
    )
