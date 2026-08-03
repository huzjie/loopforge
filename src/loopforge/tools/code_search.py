"""``search_code`` 工具 —— grep / glob 代码检索（``PURE``）。

grep 语义：按 ``query`` 逐行匹配，返回 ``file:line: text``；
glob 语义：按 ``glob`` 过滤文件名（如 ``**/*.py``），只列文件名。
两者可组合：先按 glob 选文件，再在其中 grep。
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from loopforge.core.enums import SideEffectClass
from loopforge.core.hashing import sha256_hex
from loopforge.tools.base import BaseTool, ProbeResult, StepIntent, ToolContext, ToolResult
from loopforge.executors.workspace import DEFAULT_IGNORED_DIRS

__all__ = ["SearchCodeArgs", "SearchCodeTool", "search_in_text"]


class SearchCodeArgs(BaseModel):
    """代码检索参数。"""

    query: str = Field(default="", max_length=2000, description="grep 关键字 / 正则")
    glob: str | None = Field(default=None, description="文件名 glob，如 **/*.py")
    path: str = Field(default=".", description="搜索起点（相对工作区根）")
    max_results: int = Field(default=100, ge=1, le=1000)
    ignore_case: bool = True
    regex: bool = Field(default=False, description="query 是否按正则解释")


def search_in_text(
    text: str,
    query: str,
    *,
    regex: bool = False,
    ignore_case: bool = True,
) -> list[int]:
    """在文本中搜索 query，返回命中行号（1 基）。

    Args:
        text: 文本内容。
        query: 关键字或正则。
        regex: query 是否为正则。
        ignore_case: 是否忽略大小写。

    Returns:
        命中行号列表。
    """
    flags = re.IGNORECASE if ignore_case else 0
    if regex:
        try:
            pattern = re.compile(query, flags)
        except re.error:
            pattern = re.compile(re.escape(query), flags)
    else:
        pattern = re.compile(re.escape(query), flags)
    hits: list[int] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            hits.append(index)
    return hits


class SearchCodeTool(BaseTool):
    """在工作区内搜索代码。"""

    name: ClassVar[str] = "search_code"
    description: ClassVar[str] = (
        "在工作区内检索代码：grep 文本内容（返回 file:line）或按 glob 匹配文件名。"
        "自动跳过 node_modules / .venv / __pycache__ 等目录。"
    )
    args_schema: ClassVar[type[BaseModel]] = SearchCodeArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.PURE

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """执行检索。"""
        parsed = args.model_dump()
        if ctx.executor is None or ctx.workspace is None:
            return ToolResult.failure("executor / workspace 未注入", error_code="LF-TOOL-0902")
        query: str = parsed["query"] or ""
        glob_pattern: str | None = parsed["glob"]
        if not query and not glob_pattern:
            return ToolResult.failure("query 与 glob 至少提供一个", error_code="LF-TOOL-0901")
        try:
            listing = await ctx.executor.list_dir(
                ctx.workspace, parsed["path"], depth=8
            )
        except Exception as exc:  # noqa: BLE001 - 统一转成 ToolResult
            return ToolResult.failure(
                f"搜索起点不可读：{exc}",
                error_code=getattr(exc, "code", "LF-TOOL-0902"),
            )

        root = Path(ctx.workspace.root)
        base = root / parsed["path"].lstrip("/\\").replace("\\", "/")
        if not base.exists():
            base = root
        ignored = set(DEFAULT_IGNORED_DIRS)

        matches: list[dict[str, object]] = []
        matched_files = 0
        file_count = 0
        for entry in listing.entries:
            if len(matches) >= parsed["max_results"]:
                break
            rel = entry.path
            if any(seg in ignored for seg in Path(rel).parts):
                continue
            if entry.is_dir:
                continue
            if glob_pattern and not fnmatch.fnmatch(rel, glob_pattern) and not fnmatch.fnmatch(
                Path(rel).name, glob_pattern
            ):
                continue
            file_count += 1
            abs_path = root / rel
            try:
                raw = abs_path.read_bytes()
            except (OSError, PermissionError):
                continue
            if b"\x00" in raw[:8192]:
                continue
            text = raw.decode("utf-8", errors="replace")
            if query:
                hits = search_in_text(
                    text, query, regex=parsed["regex"], ignore_case=parsed["ignore_case"]
                )
                for line_no in hits:
                    if len(matches) >= parsed["max_results"]:
                        break
                    lines = text.splitlines()
                    line_text = lines[line_no - 1] if line_no <= len(lines) else ""
                    matches.append(
                        {
                            "file": rel,
                            "line": line_no,
                            "text": line_text[:500],
                        }
                    )
                if hits:
                    matched_files += 1
            else:
                matches.append({"file": rel, "line": 0, "text": ""})
                matched_files += 1

        output_lines = [
            f"{item['file']}:{item['line']}: {item['text']}" if item["line"] else item["file"]
            for item in matches[: parsed["max_results"]]
        ]
        output = "\n".join(output_lines) if output_lines else "(无匹配)"
        if len(matches) >= parsed["max_results"]:
            output += "\n...[结果已达上限，请缩小范围]"
        return ToolResult(
            ok=True,
            output=output,
            data={
                "matches": matches[: parsed["max_results"]],
                "files_scanned": file_count,
                "files_matched": matched_files,
                "result_count": len(matches),
            },
            bytes_out=len(output.encode("utf-8")),
        )

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """PURE 工具无需探测。"""
        _ = (ctx, intent)
        return ProbeResult(
            executed=False,
            confidence="high",
            evidence="search_code 是 PURE 工具，无副作用，无需探测",
        )
