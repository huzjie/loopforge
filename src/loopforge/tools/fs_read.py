"""``read_file`` 工具 —— 读取文本文件（``PURE``，无条件可重放）。"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from loopforge.constants import MAX_TOOL_OUTPUT_BYTES
from loopforge.core.enums import SideEffectClass
from loopforge.tools.base import BaseTool, ProbeResult, StepIntent, ToolContext, ToolResult


class ReadFileArgs(BaseModel):
    """读取文件参数。"""

    path: str = Field(min_length=1, description="相对工作区根的文件路径")
    max_bytes: int = Field(
        default=MAX_TOOL_OUTPUT_BYTES, ge=1024, le=8_388_608, description="最多读取字节数"
    )


class ReadFileTool(BaseTool):
    """读取工作区内的文本文件。"""

    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "读取工作区内的一个文本文件。返回文件内容与元信息（行数 / 大小 / sha256）。"
        "超过 max_bytes 的内容会被截断并在 truncated 标记。"
    )
    args_schema: ClassVar[type[BaseModel]] = ReadFileArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.PURE

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """读取文件。"""
        parsed = args.model_dump()
        if ctx.executor is None:
            return ToolResult.failure("executor 未注入，无法读取文件", error_code="LF-TOOL-0902")
        try:
            content = await ctx.executor.read_file(
                ctx.workspace, parsed["path"], max_bytes=parsed["max_bytes"]
            )
        except Exception as exc:  # noqa: BLE001 - 统一转成 ToolResult
            return ToolResult.failure(
                f"读取失败：{exc}",
                error_code=getattr(exc, "code", "LF-TOOL-0902"),
            )
        return ToolResult(
            ok=True,
            output=content.content,
            data={
                "path": content.path,
                "size_bytes": content.size_bytes,
                "line_count": content.line_count,
                "sha256": content.sha256,
                "is_binary": content.is_binary,
                "truncated": content.truncated,
            },
            truncated=content.truncated,
            bytes_out=len(content.content.encode("utf-8")),
        )

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """PURE 工具无需探测。"""
        _ = (ctx, intent)
        return ProbeResult(
            executed=False,
            confidence="high",
            evidence="read_file 是 PURE 工具，无副作用，无需探测",
        )
