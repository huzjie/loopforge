"""``TesterRole`` —— 设计补齐测试、跑验证流水线、解析失败归因。

产出：:class:`TestReport`（测试结论 + 失败归因）。
工具边界（§11.9）：读工具 + ``write_file``（补测试）+ ``run_tests`` +
``run_shell``。
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import RoleName
from loopforge.roles.base import BaseRole, RoleContext, RoleOutput
from loopforge.roles.prompts import render_prompt
from loopforge.providers.types import ChatResult, Message

__all__ = ["TestFailure", "TestReport", "TesterRole"]


class TestFailure(BaseModel):
    """一条测试失败归因。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="用例名")
    reason: str = Field(default="")
    category: str = Field(
        default="assert", pattern="^(compile|assert|timeout|environment|dependency)$"
    )
    suggestion: str = Field(default="")


class TestReport(BaseModel):
    """Tester 的强类型结构化产出。"""

    model_config = ConfigDict(extra="forbid")

    passed: bool = Field(default=False)
    summary: str = Field(default="")
    tests_added: list[str] = Field(default_factory=list)
    failures: list[TestFailure] = Field(default_factory=list)
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    duration_ms: int = Field(default=0, ge=0)


class TesterRole(BaseRole):
    """测试者：补测试 → 跑验证 → 归因失败。"""

    name: ClassVar[RoleName] = RoleName.TESTER
    allowed_tools: ClassVar[tuple[str, ...]] = (
        "read_file",
        "list_dir",
        "search_code",
        "write_file",
        "run_tests",
        "run_shell",
    )
    prompt_file: ClassVar[str] = "tester.md"

    def build_messages(self, ctx: RoleContext) -> list[Message]:
        """组装 Tester 消息。"""
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
        test_results = ctx.metadata.get("test_results", "") or ""
        system = render_prompt(
            self.prompt_file,
            task_title=ctx.task_title,
            task_body=ctx.task_body,
            previous_output=previous,
            test_results=test_results,
        ).text
        user = (
            f"Run ID：{ctx.run_id}\nTask ID：{ctx.task_id or ''}\n"
            f"任务：{ctx.task_title}\n请输出测试结论 JSON。"
        )
        return [Message.system(system), Message.user(user)]

    def parse_output(self, result: ChatResult) -> RoleOutput:
        """解析 Tester 输出为 :class:`TestReport`。"""
        data = self.extract_json(result.content)
        report = TestReport.model_validate(data)
        return RoleOutput(
            role=self.name,
            text=report.summary,
            structured=report.model_dump(),
            usage=result.usage,
            finished=report.passed,
            handoff_to=RoleName.CODER if not report.passed else RoleName.SUMMARIZER,
        )
