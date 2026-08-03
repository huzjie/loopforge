"""``ReviewerRole`` —— 审阅 diff，产出审批结论。

**零写权限**（§11.9 / G3 闸门 #6）：``allowed_tools`` 只有只读工具，
任何写工具（``write_file`` / ``edit_file`` / ``apply_patch`` / ``run_shell`` /
``git_ops`` 的写操作）都会被 :func:`assert_role_can_tool` 拒绝并发布
``POLICY_DENIED`` 事件。这不是 prompt 约束，是**运行期硬边界**。
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import RoleName
from loopforge.roles.base import BaseRole, RoleContext, RoleOutput
from loopforge.roles.prompts import render_prompt
from loopforge.providers.types import ChatResult, Message

__all__ = ["ReviewFinding", "ReviewVerdict", "ReviewerRole"]


class ReviewFinding(BaseModel):
    """一条评审意见。"""

    model_config = ConfigDict(extra="forbid")

    severity: str = Field(default="warning", pattern="^(critical|warning|nit)$")
    location: str = Field(default="")
    message: str = Field(min_length=1)
    suggestion: str = Field(default="")


class ReviewVerdict(BaseModel):
    """Reviewer 的强类型结构化产出。"""

    model_config = ConfigDict(extra="forbid")

    approved: bool = Field(default=False)
    summary: str = Field(default="")
    findings: list[ReviewFinding] = Field(default_factory=list)
    must_fix: list[ReviewFinding] = Field(default_factory=list)

    @property
    def critical_count(self) -> int:
        """critical 级问题总数。"""
        return sum(1 for f in self.findings if f.severity == "critical") + len(self.must_fix)

    @property
    def blocking(self) -> bool:
        """是否存在必须修复的问题（不可放行）。"""
        return bool(self.must_fix) or any(f.severity == "critical" for f in self.findings)


class ReviewerRole(BaseRole):
    """评审者：只读审阅，产出 :class:`ReviewVerdict`。"""

    name: ClassVar[RoleName] = RoleName.REVIEWER
    allowed_tools: ClassVar[tuple[str, ...]] = ("read_file", "list_dir", "search_code")
    prompt_file: ClassVar[str] = "reviewer.md"

    def build_messages(self, ctx: RoleContext) -> list[Message]:
        """组装 Reviewer 消息（含 diff / 改动说明）。"""
        context_text = ""
        if ctx.context_pack is not None:
            pack = ctx.context_pack
            if hasattr(pack, "render"):
                context_text = pack.render()
        previous = ""
        if ctx.previous_output is not None:
            previous = (
                ctx.previous_output.text
                or str(ctx.previous_output.structured.get("description", ""))
            )
        diff = ctx.metadata.get("diff", "") or ""
        system = render_prompt(
            self.prompt_file,
            task_title=ctx.task_title,
            task_body=ctx.task_body,
            previous_output=previous,
            diff=diff,
        ).text
        user = (
            f"Run ID：{ctx.run_id}\nTask ID：{ctx.task_id or ''}\n"
            f"请审阅以下改动并输出 JSON 结论。\n\n{diff[:8000]}"
        )
        return [Message.system(system), Message.user(user)]

    def parse_output(self, result: ChatResult) -> RoleOutput:
        """解析 Reviewer 输出为 :class:`ReviewVerdict`。"""
        data = self.extract_json(result.content)
        verdict = ReviewVerdict.model_validate(data)
        return RoleOutput(
            role=self.name,
            text=verdict.summary,
            structured=verdict.model_dump(),
            usage=result.usage,
            finished=verdict.approved,
            handoff_to=RoleName.CODER if verdict.blocking else RoleName.TESTER,
        )
