"""Run DTO。

对应模型：:mod:`loopforge.models.run`。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from loopforge.constants import DEFAULT_MAX_LOOPS
from loopforge.core.enums import TERMINAL_RUN_STATES, RunState
from loopforge.schemas.common import LFModel, ORMModel, PageQuery

__all__ = [
    "MAX_LOOPS_HARD_LIMIT",
    "RUN_CONTROL_ACTIONS",
    "RunCreate",
    "RunUpdate",
    "RunRead",
    "RunDetail",
    "RunStateChange",
    "RunControlAction",
    "RunQuery",
    "RunStats",
]

#: ``max_loops`` 的硬上限 —— 再高就不是「长程」而是「失控」了。
#: 配置层的 ``orchestration.max_loops`` 只能小于等于它。
MAX_LOOPS_HARD_LIMIT: int = 10_000

#: 允许的 Run 控制指令。
RUN_CONTROL_ACTIONS: tuple[str, ...] = ("pause", "resume", "cancel", "halt")


class RunCreate(LFModel):
    """创建一次 Run。"""

    project_id: str = Field(min_length=1, max_length=26)
    repo_id: str | None = Field(default=None, max_length=26)
    goal: str = Field(min_length=1, max_length=20000, description="自然语言总目标")
    constraints: dict[str, Any] = Field(
        default_factory=dict, description="硬约束：禁改路径、必过测试、时间窗等"
    )
    max_loops: int = Field(default=DEFAULT_MAX_LOOPS, ge=1, le=MAX_LOOPS_HARD_LIMIT)
    budget_usd: Decimal | None = Field(
        default=None, ge=Decimal("0"), description="本 Run 的预算上限；None 用全局默认"
    )
    policy_name: str | None = Field(
        default=None, max_length=100, description="使用的策略名；None 用 default"
    )
    config_overrides: dict[str, Any] = Field(
        default_factory=dict, description="Run 级配置覆盖（配置第 6 层）"
    )
    auto_start: bool = Field(default=True, description="创建后是否立即进入 PLANNING")

    @field_validator("goal")
    @classmethod
    def _check_goal(cls, value: str) -> str:
        """目标不能只有空白。

        :raises ValueError: 目标为空。
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("goal 不能为空")
        return stripped


class RunUpdate(LFModel):
    """更新 Run 的可变字段（不含状态 —— 状态只能走状态机接口）。"""

    goal: str | None = Field(default=None, min_length=1, max_length=20000)
    constraints: dict[str, Any] | None = Field(default=None)
    max_loops: int | None = Field(default=None, ge=1, le=MAX_LOOPS_HARD_LIMIT)

    def changed_fields(self) -> dict[str, Any]:
        """返回本次真正要更新的字段。"""
        return self.model_dump(exclude_none=True)


class RunControlAction(LFModel):
    """Run 控制指令（pause / resume / cancel / halt）。"""

    action: str = Field(description="pause / resume / cancel / halt")
    reason: str = Field(default="", max_length=2000)
    force: bool = Field(
        default=False, description="cancel 时是否跳过优雅收尾直接终止"
    )

    @field_validator("action")
    @classmethod
    def _check_action(cls, value: str) -> str:
        """校验控制指令合法。

        :raises ValueError: 指令未知。
        """
        normalized = value.strip().lower()
        if normalized not in RUN_CONTROL_ACTIONS:
            raise ValueError(f"action 必须是 {list(RUN_CONTROL_ACTIONS)} 之一")
        return normalized


class RunStateChange(LFModel):
    """状态迁移请求（内部服务间使用，不对外暴露）。"""

    from_state: RunState
    to_state: RunState
    reason: str = Field(default="", max_length=2000)
    error_code: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _check_transition(self) -> Self:
        """终态不允许再迁出，且不允许自迁移。

        :raises ValueError: 迁移非法。
        """
        if self.from_state == self.to_state:
            raise ValueError("from_state 与 to_state 相同，属于无效迁移")
        if self.from_state in TERMINAL_RUN_STATES:
            raise ValueError(f"{self.from_state} 是终态，不允许再迁出")
        return self


class RunRead(ORMModel):
    """Run 读模型。"""

    id: str
    project_id: str
    repo_id: str | None = None
    goal: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    state: RunState = RunState.CREATED
    loop_index: int = 0
    milestone_index: int = 0
    max_loops: int = DEFAULT_MAX_LOOPS
    budget_id: str | None = None
    policy_id: str | None = None
    recovery_count: int = 0
    error_code: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def is_terminal(self) -> bool:
        """是否已进入终态。"""
        return self.state in TERMINAL_RUN_STATES

    @property
    def loops_remaining(self) -> int:
        """剩余循环数。"""
        return max(0, self.max_loops - self.loop_index)


class RunStats(LFModel):
    """Run 的聚合统计（详情页顶部卡片）。"""

    task_total: int = Field(default=0, ge=0)
    task_done: int = Field(default=0, ge=0)
    task_failed: int = Field(default=0, ge=0)
    task_active: int = Field(default=0, ge=0)
    step_total: int = Field(default=0, ge=0)
    step_unconfirmed: int = Field(default=0, ge=0)
    event_count: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0E-8"), ge=Decimal("0"))
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    pending_approvals: int = Field(default=0, ge=0)

    @property
    def completion_ratio(self) -> float:
        """已完成任务占比（无任务时为 0.0）。"""
        if self.task_total <= 0:
            return 0.0
        return self.task_done / self.task_total


class RunDetail(RunRead):
    """Run 详情（带统计与配置快照）。"""

    stats: RunStats = Field(default_factory=RunStats)
    config_snapshot: dict[str, Any] = Field(default_factory=dict)


class RunQuery(PageQuery):
    """Run 列表查询参数。"""

    project_id: str | None = Field(default=None, max_length=26)
    state: RunState | None = Field(default=None)
    active_only: bool = Field(default=False, description="只看活跃 Run")
    created_after: datetime | None = Field(default=None)
    created_before: datetime | None = Field(default=None)

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        """校验时间窗不倒挂。

        :raises ValueError: 时间窗非法。
        """
        if (
            self.created_after is not None
            and self.created_before is not None
            and self.created_after > self.created_before
        ):
            raise ValueError("created_after 必须早于或等于 created_before")
        return self
