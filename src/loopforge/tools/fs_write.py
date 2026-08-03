"""``write_file`` 工具 —— 原子写入文件（``IDEMPOTENT``，probe 比对 sha256）。"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from loopforge.core.enums import SideEffectClass
from loopforge.core.hashing import sha256_hex
from loopforge.tools.base import BaseTool, ProbeResult, StepIntent, ToolContext, ToolResult


class WriteFileArgs(BaseModel):
    """写入文件参数。"""

    path: str = Field(min_length=1, description="相对工作区根的文件路径")
    content: str = Field(default="", description="文件内容")
    encoding: str = Field(default="utf-8", description="文本编码")


class WriteFileTool(BaseTool):
    """原子写入文本文件。"""

    name: ClassVar[str] = "write_file"
    description: ClassVar[str] = (
        "原子写入一个文本文件（临时文件 + rename，同目录）。"
        "返回目标文件的 sha256，用于幂等探测比对。"
    )
    args_schema: ClassVar[type[BaseModel]] = WriteFileArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.IDEMPOTENT

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """写入文件。"""
        parsed = args.model_dump()
        if ctx.executor is None or ctx.workspace is None:
            return ToolResult.failure("executor / workspace 未注入", error_code="LF-TOOL-0902")
        try:
            stat = await ctx.executor.write_file(
                ctx.workspace,
                parsed["path"],
                parsed["content"],
                encoding=parsed["encoding"],
            )
        except Exception as exc:  # noqa: BLE001 - 统一转成 ToolResult
            return ToolResult.failure(
                f"写入失败：{exc}",
                error_code=getattr(exc, "code", "LF-TOOL-0902"),
            )
        return ToolResult(
            ok=True,
            output=f"已写入 {stat.path}（{stat.size_bytes} 字节，sha256={stat.sha256}）",
            data={
                "path": stat.path,
                "size_bytes": stat.size_bytes,
                "sha256": stat.sha256,
                "created": stat.created,
            },
            external_ref=stat.sha256,
        )

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """比对目标文件 sha256 与意图中的期望值。"""
        path = str(intent.args.get("path", ""))
        if not path or ctx.executor is None or ctx.workspace is None:
            return ProbeResult(
                executed=False,
                confidence="low",
                evidence="意图缺少 path 或 executor 未注入",
            )
        try:
            stat = await ctx.executor.stat(ctx.workspace, path)
        except Exception as exc:  # noqa: BLE001 - 探测失败不算已执行
            return ProbeResult(
                executed=False,
                confidence="medium",
                evidence=f"stat 失败：{exc}",
            )
        if not stat.exists:
            return ProbeResult(
                executed=False,
                confidence="high",
                evidence=f"目标文件不存在：{path}",
            )
        expected = intent.expected_sha256
        if not expected:
            content = str(intent.args.get("content", ""))
            expected = sha256_hex(content.encode("utf-8"))
        executed = bool(expected) and stat.sha256 == expected
        return ProbeResult(
            executed=executed,
            external_ref=stat.sha256,
            confidence="high" if executed else "medium",
            evidence=(
                f"目标文件 sha256={stat.sha256}，期望 {expected}"
                if executed
                else f"目标文件 sha256={stat.sha256}，与期望 {expected} 不一致"
            ),
        )
