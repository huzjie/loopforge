"""Task DTO。

对应模型：:mod:`loopforge.models.task`。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from loopforge.constants import DEFAULT_MAX_ATTEMPTS, DEFAULT_MAX_RECLAIMS
from loopforge.core.enums import TERMINAL_TASK_STATES, TaskState
from loopforge.schemas.common import LFModel, ORMModel, PageQuery

__all__ = [
    "TASK_KEY_PATTERN",
    "TaskCreate",
    "TaskUpdate",
    "TaskRead",
    "TaskDetail",
    "TaskStateChange",
    "TaskQuery",
    "TaskGraphNode",
    "TaskGraph",
]

#: ``task_key`` 形态：``T-0007`` / ``FIX-12`` 这类 Run 内稳定短标识。
TASK_KEY_PATTERN: re.Pattern[str] = re.compile(r"^[A-Z][A-Z0-9]{0,15}-\d{1,6}$")


class TaskCreate(LFModel):
    """创建 Task。"""

    run_id: str = Field(min_length=1, max_length=26)
    task_key: str = Field(min_length=3, max_length=64, description="Run 内稳定标识，如 T-0007")
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=100_000)
    repo_id: str | None = Field(default=None, max_length=26)
    priority: int = Field(default=100, ge=0, le=10_000, description="数值越大越优先")
    depends_on: list[str] = Field(default_factory=list, description="前置 task_id 列表")
    touch_paths: list[str] = Field(
        default_factory=list, description="预计改动路径，用于并发冲突检测"
    )
    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1, le=100)
    max_reclaims: int = Field(default=DEFAULT_MAX_RECLAIMS, ge=0, le=100)
    external_issue_number: int | None = Field(default=None, ge=1)

    @field_validator("task_key")
    @classmethod
    def _check_key(cls, value: str) -> str:
        """校验 task_key 形态。

        :raises ValueError: 形态非法。
        """
        normalized = value.strip().upper()
        if not TASK_KEY_PATTERN.match(normalized):
            raise ValueError("task_key 形如 T-0007：大写前缀 + 连字符 + 数字")
        return normalized

    @field_validator("depends_on")
    @classmethod
    def _dedupe_deps(cls, value: list[str]) -> list[str]:
        """依赖去重并保持原顺序。"""
        seen: set[str] = set()
        unique: list[str] = []
        for item in value:
            trimmed = item.strip()
            if trimmed and trimmed not in seen:
                seen.add(trimmed)
                unique.append(trimmed)
        return unique


class TaskUpdate(LFModel):
    """更新 Task 的可变字段（状态另走状态机接口）。"""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, max_length=100_000)
    priority: int | None = Field(default=None, ge=0, le=10_000)
    depends_on: list[str] | None = Field(default=None)
    touch_paths: list[str] | None = Field(default=None)
    max_attempts: int | None = Field(default=None, ge=1, le=100)
    blocked_reason: str | None = Field(default=None, max_length=4000)

    def changed_fields(self) -> dict[str, Any]:
        """返回本次真正要更新的字段。"""
        return self.model_dump(exclude_none=True)


class TaskStateChange(LFModel):
    """Task 的 CAS 状态迁移请求。

    ``expected_version`` 是 CAS 的比较位 —— 调用方必须带上自己读到的版本号，
    否则并发下两个 Worker 会把对方的迁移覆盖掉。
    """

    from_state: TaskState
    to_state: TaskState
    expected_version: int = Field(ge=0)
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _check_transition(self) -> Self:
        """终态不可迁出、不可自迁移。

        :raises ValueError: 迁移非法。
        """
        if self.from_state == self.to_state:
            raise ValueError("from_state 与 to_state 相同，属于无效迁移")
        if self.from_state in TERMINAL_TASK_STATES:
            raise ValueError(f"{self.from_state} 是终态，不允许再迁出")
        return self


class TaskRead(ORMModel):
    """Task 读模型。"""

    id: str
    run_id: str
    repo_id: str | None = None
    task_key: str
    title: str
    body: str = ""
    state: TaskState = TaskState.CREATED
    priority: int = 100
    depends_on: list[str] = Field(default_factory=list)
    touch_paths: list[str] = Field(default_factory=list)
    version: int = 0
    attempt_count: int = 0
    reclaim_count: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    max_reclaims: int = DEFAULT_MAX_RECLAIMS
    current_lease_id: str | None = None
    fence_token: int = 0
    external_issue_number: int | None = None
    branch_name: str | None = None
    pr_url: str | None = None
    blocked_reason: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @property
    def is_terminal(self) -> bool:
        """是否已进入终态。"""
        return self.state in TERMINAL_TASK_STATES

    @property
    def attempts_remaining(self) -> int:
        """剩余尝试次数。"""
        return max(0, self.max_attempts - self.attempt_count)


class TaskDetail(TaskRead):
    """Task 详情（带 Step 与成本摘要）。"""

    step_count: int = Field(default=0, ge=0)
    unconfirmed_step_count: int = Field(default=0, ge=0)
    cost_usd: str = Field(default="0", description="十进制字符串，避免 JSON 浮点误差")
    lease_worker_id: str | None = Field(default=None)


class TaskQuery(PageQuery):
    """Task 列表查询参数。"""

    run_id: str | None = Field(default=None, max_length=26)
    state: TaskState | None = Field(default=None)
    leasable_only: bool = Field(default=False, description="只看可被领取的任务")
    q: str | None = Field(default=None, max_length=200, description="标题模糊搜索")


class TaskGraphNode(LFModel):
    """任务依赖图的一个节点。"""

    id: str
    task_key: str
    title: str
    state: TaskState
    priority: int = 100
    depends_on: list[str] = Field(default_factory=list)

    @property
    def is_root(self) -> bool:
        """是否是无前置依赖的根节点。"""
        return not self.depends_on


class TaskGraph(LFModel):
    """整个 Run 的任务依赖图。"""

    run_id: str
    nodes: list[TaskGraphNode] = Field(default_factory=list)

    def edges(self) -> list[tuple[str, str]]:
        """展开成 ``(前置, 后继)`` 的边列表。

        :returns: 有向边列表，只包含图内存在的节点。
        """
        known = {node.id for node in self.nodes}
        result: list[tuple[str, str]] = []
        for node in self.nodes:
            for parent in node.depends_on:
                if parent in known:
                    result.append((parent, node.id))
        return result

    def detect_cycle(self) -> list[str]:
        """检测依赖环。

        用迭代式着色 DFS（0=未访问 / 1=在栈上 / 2=已完成），避免递归在超深依赖
        链上炸栈。

        :returns: 环上节点 ID 列表；无环时为空列表。
        """
        adjacency: dict[str, list[str]] = {node.id: [] for node in self.nodes}
        for parent, child in self.edges():
            adjacency[parent].append(child)

        color: dict[str, int] = {node_id: 0 for node_id in adjacency}
        parent_of: dict[str, str | None] = {node_id: None for node_id in adjacency}

        for start in adjacency:
            if color[start] != 0:
                continue
            stack: list[tuple[str, int]] = [(start, 0)]
            while stack:
                node_id, child_index = stack.pop()
                if child_index == 0:
                    color[node_id] = 1
                children = adjacency[node_id]
                if child_index < len(children):
                    stack.append((node_id, child_index + 1))
                    nxt = children[child_index]
                    if color[nxt] == 1:
                        cycle = [nxt]
                        cursor: str | None = node_id
                        while cursor is not None and cursor != nxt:
                            cycle.append(cursor)
                            cursor = parent_of[cursor]
                        cycle.append(nxt)
                        cycle.reverse()
                        return cycle
                    if color[nxt] == 0:
                        parent_of[nxt] = node_id
                        stack.append((nxt, 0))
                else:
                    color[node_id] = 2
        return []
