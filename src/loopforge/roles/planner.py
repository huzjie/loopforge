"""``PlannerRole`` —— 把 Run 目标分解为任务 DAG。

产出：:class:`PlanResult`（``TaskDraft[]`` + 里程碑 + 初始 Pinned Constraints）。
结构化结果以 ``RoleOutput.structured`` 承载，WP4 据此落库 Task / 里程碑 /
Pinned Constraint。
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import RoleName
from loopforge.roles.base import BaseRole, MilestoneDraft, RoleContext, RoleOutput, TaskDraft
from loopforge.roles.prompts import render_prompt
from loopforge.providers.types import ChatResult, Message

__all__ = ["PlanResult", "PlannerRole"]


class PlanResult(BaseModel):
    """Planner 的强类型结构化产出。"""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", description="规划说明")
    tasks: list[TaskDraft] = Field(default_factory=list, description="任务 DAG")
    milestones: list[MilestoneDraft] = Field(default_factory=list, description="里程碑定义")
    pinned_constraints: list[str] = Field(default_factory=list, description="初始 Pinned Constraints")


class PlannerRole(BaseRole):
    """规划者：Run goal → TaskDraft[] + 里程碑 + Pinned Constraints。

    工具边界（§11.9）：只读工具 ``read_file`` / ``list_dir`` / ``search_code``，
    拿不到 ``run_shell`` —— 规划者只观察，不执行。
    """

    name: ClassVar[RoleName] = RoleName.PLANNER
    allowed_tools: ClassVar[tuple[str, ...]] = ("read_file", "list_dir", "search_code")
    prompt_file: ClassVar[str] = "planner.md"

    def build_messages(self, ctx: RoleContext) -> list[Message]:
        """组装 Planner 消息：system prompt + 目标 / 约束 / 上下文。"""
        context_text = ""
        if ctx.context_pack is not None:
            pack = ctx.context_pack
            if hasattr(pack, "render"):
                context_text = pack.render()
            elif isinstance(pack, list):
                context_text = "\n\n".join(str(m) for m in pack)
        constraints = "\n".join(ctx.metadata.get("constraints", [])) or "（无显式约束）"
        system = render_prompt(
            self.prompt_file,
            goal=ctx.goal,
            constraints=constraints,
            context=context_text,
        ).text
        user = f"Run ID：{ctx.run_id}\n目标：{ctx.goal}\n请输出任务分解 JSON。"
        return [Message.system(system), Message.user(user)]

    def parse_output(self, result: ChatResult) -> RoleOutput:
        """解析 Planner 输出为 :class:`PlanResult`。"""
        data = self.extract_json(result.content)
        plan = PlanResult.model_validate(data)
        output = RoleOutput(
            role=self.name,
            text=plan.summary,
            structured=plan.model_dump(),
            pinned_constraints=list(plan.pinned_constraints),
            usage=result.usage,
            finished=True,
            handoff_to=None,
        )
        return output
