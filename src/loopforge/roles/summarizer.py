"""``SummarizerRole`` —— 递归压缩、里程碑摘要、PR 描述、约束保真度校验。

**纯文本角色**（§11.9）：``allowed_tools = ()`` —— 不调任何工具。
产出：:class:`SummaryResult`。
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import RoleName
from loopforge.roles.base import BaseRole, RoleContext, RoleOutput
from loopforge.roles.prompts import render_prompt
from loopforge.providers.types import ChatResult, Message

__all__ = ["PrDescription", "ConstraintFidelity", "SummaryResult", "SummarizerRole"]


class PrDescription(BaseModel):
    """PR 描述。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="")
    body: str = Field(default="")


class ConstraintFidelity(BaseModel):
    """约束保真度校验结论。"""

    model_config = ConfigDict(extra="forbid")

    satisfied: bool = Field(default=False)
    violations: list[str] = Field(default_factory=list)


class SummaryResult(BaseModel):
    """Summarizer 的强类型结构化产出。"""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="")
    milestone_summaries: list[str] = Field(default_factory=list)
    pr_description: PrDescription = Field(default_factory=PrDescription)
    constraint_fidelity: ConstraintFidelity = Field(default_factory=ConstraintFidelity)


class SummarizerRole(BaseRole):
    """总结者：纯文本角色，不调工具。"""

    name: ClassVar[RoleName] = RoleName.SUMMARIZER
    allowed_tools: ClassVar[tuple[str, ...]] = ()
    prompt_file: ClassVar[str] = "summarizer.md"

    def build_messages(self, ctx: RoleContext) -> list[Message]:
        """组装 Summarizer 消息。"""
        content = (
            ctx.metadata.get("content", "")
            or (ctx.context_pack.render() if ctx.context_pack is not None else "")
        )
        constraints = "\n".join(ctx.metadata.get("constraints", [])) or "（无）"
        milestone = ctx.metadata.get("milestone", "本阶段")
        system = render_prompt(
            self.prompt_file,
            milestone=milestone,
            content=content,
            constraints=constraints,
        ).text
        user = (
            f"Run ID：{ctx.run_id}\n请输出阶段总结 JSON。"
        )
        return [Message.system(system), Message.user(user)]

    def parse_output(self, result: ChatResult) -> RoleOutput:
        """解析 Summarizer 输出为 :class:`SummaryResult`。"""
        data = self.extract_json(result.content)
        summary = SummaryResult.model_validate(data)
        return RoleOutput(
            role=self.name,
            text=summary.summary,
            structured=summary.model_dump(),
            usage=result.usage,
            finished=summary.constraint_fidelity.satisfied,
            handoff_to=None,
        )
