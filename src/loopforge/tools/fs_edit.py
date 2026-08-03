"""``edit_file`` 工具 —— 字符串替换编辑（``IDEMPOTENT``，probe 比对 sha256）。

与 ``write_file`` 的差别：``edit_file`` 只做**局部替换**，需要目标文件存在，
且 ``old_string`` 必须精确命中（可选 ``replace_all`` 控制多处替换）。
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from loopforge.core.enums import SideEffectClass
from loopforge.core.hashing import sha256_hex
from loopforge.tools.base import BaseTool, ProbeResult, StepIntent, ToolContext, ToolResult


class EditFileArgs(BaseModel):
    """编辑文件参数。"""

    path: str = Field(min_length=1, description="相对工作区根的文件路径")
    old_string: str = Field(min_length=1, description="被替换的原文（必须精确命中）")
    new_string: str = Field(default="", description="替换后的新文")
    replace_all: bool = Field(default=False, description="是否替换全部命中处")


class EditFileTool(BaseTool):
    """在工作区内做局部字符串替换。"""

    name: ClassVar[str] = "edit_file"
    description: ClassVar[str] = (
        "对工作区内的文件做精确字符串替换（不是正则）。"
        "old_string 必须逐字命中；默认只替换第一处，replace_all=true 替换全部。"
        "返回替换次数与新文件 sha256。"
    )
    args_schema: ClassVar[type[BaseModel]] = EditFileArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.IDEMPOTENT

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """执行编辑。"""
        parsed = args.model_dump()
        if ctx.executor is None or ctx.workspace is None:
            return ToolResult.failure("executor / workspace 未注入", error_code="LF-TOOL-0902")
        try:
            current = await ctx.executor.read_file(ctx.workspace, parsed["path"])
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(
                f"读取失败：{exc}",
                error_code=getattr(exc, "code", "LF-TOOL-0902"),
            )
        if current.is_binary:
            return ToolResult.failure("不支持编辑二进制文件", error_code="LF-TOOL-0902")

        old = parsed["old_string"]
        new = parsed["new_string"]
        text = current.content
        count = text.count(old)
        if count == 0:
            return ToolResult.failure(
                f"old_string 未命中：{parsed['path']}",
                error_code="LF-TOOL-0901",
                path=parsed["path"],
            )
        if count > 1 and not parsed["replace_all"]:
            return ToolResult.failure(
                f"old_string 命中 {count} 处，请用 replace_all=true 或提供更长上下文",
                error_code="LF-TOOL-0901",
                hits=count,
                path=parsed["path"],
            )
        updated = text.replace(old, new) if parsed["replace_all"] else text.replace(old, new, 1)
        try:
            stat = await ctx.executor.write_file(
                ctx.workspace, parsed["path"], updated, encoding=current.encoding
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(
                f"写入失败：{exc}",
                error_code=getattr(exc, "code", "LF-TOOL-0902"),
            )
        return ToolResult(
            ok=True,
            output=f"已替换 {count if parsed['replace_all'] else 1} 处：{parsed['path']}",
            data={
                "path": stat.path,
                "replacements": count if parsed["replace_all"] else 1,
                "sha256": stat.sha256,
            },
            external_ref=stat.sha256,
        )

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """比对目标文件 sha256 与意图中的期望值。"""
        path = str(intent.args.get("path", ""))
        if not path or ctx.executor is None or ctx.workspace is None:
            return ProbeResult(executed=False, confidence="low", evidence="意图缺少 path")
        try:
            stat = await ctx.executor.stat(ctx.workspace, path)
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(executed=False, confidence="medium", evidence=f"stat 失败：{exc}")
        if not stat.exists:
            return ProbeResult(executed=False, confidence="high", evidence=f"文件不存在：{path}")
        expected = intent.expected_sha256
        if not expected:
            # 由参数推算最终内容 hash：读原文件 → 替换 → hash。
            try:
                current = await ctx.executor.read_file(ctx.workspace, path)
                old = str(intent.args.get("old_string", ""))
                new = str(intent.args.get("new_string", ""))
                replace_all = bool(intent.args.get("replace_all", False))
                updated = (
                    current.content.replace(old, new)
                    if replace_all
                    else current.content.replace(old, new, 1)
                )
                expected = sha256_hex(updated.encode("utf-8"))
            except Exception:  # noqa: BLE001 - 推算失败降级
                expected = ""
        executed = bool(expected) and stat.sha256 == expected
        return ProbeResult(
            executed=executed,
            external_ref=stat.sha256,
            confidence="high" if executed else "medium",
            evidence=f"文件 sha256={stat.sha256}，期望 {expected}",
        )
