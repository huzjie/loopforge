"""``list_dir`` 工具 —— 列举目录（``PURE``）。"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from loopforge.core.enums import SideEffectClass
from loopforge.tools.base import BaseTool, ProbeResult, StepIntent, ToolContext, ToolResult


class ListDirArgs(BaseModel):
    """列举目录参数。"""

    path: str = Field(default=".", description="相对工作区根的目录路径")
    depth: int = Field(default=1, ge=1, le=8, description="递归深度，1 表示只列当前层")


class ListDirTool(BaseTool):
    """列举工作区目录。"""

    name: ClassVar[str] = "list_dir"
    description: ClassVar[str] = (
        "列举工作区内的目录。返回条目树（目录优先 + 名称字典序），"
        "depth 控制递归层级，自动跳过 node_modules / .venv / __pycache__ 等目录。"
    )
    args_schema: ClassVar[type[BaseModel]] = ListDirArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.PURE

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """列举目录。"""
        parsed = args.model_dump()
        if ctx.executor is None:
            return ToolResult.failure("executor 未注入，无法列目录", error_code="LF-TOOL-0902")
        try:
            listing = await ctx.executor.list_dir(
                ctx.workspace, parsed["path"], depth=parsed["depth"]
            )
        except Exception as exc:  # noqa: BLE001 - 统一转成 ToolResult
            return ToolResult.failure(
                f"列目录失败：{exc}",
                error_code=getattr(exc, "code", "LF-TOOL-0902"),
            )
        entries = [entry.model_dump() for entry in listing.entries]
        return ToolResult(
            ok=True,
            output=listing.as_tree(),
            data={
                "path": listing.path,
                "total": listing.total,
                "truncated": listing.truncated,
                "entries": entries,
            },
            truncated=listing.truncated,
        )

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """PURE 工具无需探测。"""
        _ = (ctx, intent)
        return ProbeResult(
            executed=False,
            confidence="high",
            evidence="list_dir 是 PURE 工具，无副作用，无需探测",
        )
