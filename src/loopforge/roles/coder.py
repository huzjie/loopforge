"""``CoderRole`` —— 读码 → 改码 → 自查。

产出：:class:`CodeChangeResult`（改动文件清单 + 自测记录 + 自查结论）。
Coder 拥有写工具（§11.9），改完必须自查。
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import RoleName
from loopforge.roles.base import BaseRole, RoleContext, RoleOutput
from loopforge.roles.prompts import render_prompt
from loopforge.providers.types import ChatResult, Message

__all__ = ["FileChange", "CodeChangeResult", "CoderRole"]


class FileChange(BaseModel):
    """单个文件的改动记录。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="文件路径")
    action: str = Field(default="modified", pattern="^(modified|created|deleted)$")
    summary: str = Field(default="")


class CodeChangeResult(BaseModel):
    """Coder 的强类型结构化产出。"""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="改动一句话说明")
    files_changed: list[FileChange] = Field(default_factory=list)
    tests_run: list[str] = Field(default_factory=list, description="已运行的测试命令")
    self_checked: bool = Field(default=False, description="是否完成自查")
    notes: str = Field(default="")


class CoderRole(BaseRole):
    """编码者：读代码 → 改代码 → 自查。

    工具边界（§11.9）：读写工具 + shell + 测试 + git。
    """

    name: ClassVar[RoleName] = RoleName.CODER
    allowed_tools: ClassVar[tuple[str, ...]] = (
        "read_file",
        "list_dir",
        "search_code",
        "write_file",
        "edit_file",
        "apply_patch",
        "run_shell",
        "run_tests",
        "git_ops",
    )
    prompt_file: ClassVar[str] = "coder.md"

    def build_messages(self, ctx: RoleContext) -> list[Message]:
        """组装 Coder 消息。"""
        context_text = ""
        if ctx.context_pack is not None:
            pack = ctx.context_pack
            if hasattr(pack, "render"):
                context_text = pack.render()
            elif isinstance(pack, list):
                context_text = "\n\n".join(str(m) for m in pack)
        previous = ""
        if ctx.previous_output is not None:
            previous = ctx.previous_output.text or ctx.previous_output.structured.get(
                "description", ""
            )
        system = render_prompt(
            self.prompt_file,
            task_title=ctx.task_title,
            task_body=ctx.task_body,
            context=context_text,
            previous_output=previous,
        ).text
        user = (
            f"Run ID：{ctx.run_id}\nTask ID：{ctx.task_id or ''}\n"
            f"任务：{ctx.task_title}\n{ctx.task_body}\n请输出改动 JSON。"
        )
        return [Message.system(system), Message.user(user)]

    def parse_output(self, result: ChatResult) -> RoleOutput:
        """解析 Coder 输出为 :class:`CodeChangeResult`。"""
        data = self.extract_json(result.content)
        change = CodeChangeResult.model_validate(data)
        return RoleOutput(
            role=self.name,
            text=change.description,
            structured=change.model_dump(),
            usage=result.usage,
            finished=change.self_checked,
            handoff_to=RoleName.TESTER if change.self_checked else None,
        )
