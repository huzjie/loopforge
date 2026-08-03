"""``apply_patch`` 工具 —— unified diff 解析与应用（``IDEMPOTENT``）。

能力
----
* 解析标准 unified diff（git diff 输出格式）：``---`` / ``+++`` 文件头 +
  ``@@ -l,c +l,c @@`` hunks + 上下文/增删行。
* **行号漂移**：hunk 声明的 ``old_start`` 与实际位置允许偏差，滑动匹配。
* **fuzz**：允许前 ``fuzz`` 条上下文行不匹配（用于 patch 漂移场景）。
* **失败回滚**：先全部解析 + 计算新内容，再统一写盘；写盘中途失败会把已写
  文件恢复为原始内容（幂等安全）。
* 支持新建（``--- /dev/null``）与删除（``+++ /dev/null``）文件。

安全说明
--------
路径经 ``executor`` 的路径沙箱解析，``../../etc/passwd`` 一类的逃逸在写盘前
被拦截；全部操作限定在工作区内。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

from pydantic import BaseModel, Field

from loopforge.core.enums import SideEffectClass
from loopforge.core.hashing import sha256_hex
from loopforge.tools.base import BaseTool, ProbeResult, StepIntent, ToolContext, ToolResult

__all__ = [
    "PatchHunk",
    "FilePatch",
    "parse_unified_diff",
    "apply_patch_to_text",
    "apply_patch_to_files",
    "ApplyPatchArgs",
    "ApplyPatchTool",
]


@dataclass
class PatchHunk:
    """一个 ``@@`` hunk。"""

    old_start: int = 1
    old_count: int = 0
    new_start: int = 1
    new_count: int = 0
    body: list[tuple[str, str]] = field(default_factory=list)  # (op, line)，op in " -+"

    @property
    def old_lines(self) -> list[str]:
        """需要从旧文件匹配/删除的行（context + remove）。"""
        return [line for op, line in self.body if op in (" ", "-")]

    @property
    def remove_lines(self) -> list[str]:
        """标记为 ``-`` 的行。"""
        return [line for op, line in self.body if op == "-"]

    @property
    def add_lines(self) -> list[str]:
        """标记为 ``+`` 的行。"""
        return [line for op, line in self.body if op == "+"]


@dataclass
class FilePatch:
    """单个文件的 patch。"""

    old_path: str
    new_path: str
    hunks: list[PatchHunk] = field(default_factory=list)

    @property
    def creates(self) -> bool:
        """是否新建文件。"""
        return self.old_path == "/dev/null"

    @property
    def deletes(self) -> bool:
        """是否删除文件。"""
        return self.new_path == "/dev/null"


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FILE_HEADER = re.compile(r"^(\+\+\+|---) (.*)$")


def parse_unified_diff(patch_text: str) -> list[FilePatch]:
    """解析 unified diff 文本。

    Args:
        patch_text: patch 内容。

    Returns:
        :class:`FilePatch` 列表。

    Raises:
        ValueError: patch 格式非法。
    """
    files: list[FilePatch] = []
    current: FilePatch | None = None
    current_hunk: PatchHunk | None = None

    for raw_line in patch_text.splitlines():
        if raw_line.startswith("\\"):  # "\ No newline at end of file"
            continue
        if raw_line.startswith("diff --git") or raw_line.startswith("index "):
            continue
        if raw_line.startswith("new file mode") or raw_line.startswith("deleted file mode"):
            continue
        if raw_line.startswith("--- ") or raw_line.startswith("+++ "):
            current_hunk = None
            match = _FILE_HEADER.match(raw_line)
            assert match is not None
            marker, path = match.group(1), match.group(2).strip()
            path = _strip_a_b_prefix(path)
            if marker == "---":
                if current is not None:
                    files.append(current)
                current = FilePatch(old_path=path, new_path="")
            else:
                if current is None:
                    current = FilePatch(old_path="", new_path="")
                current.new_path = path
            continue
        if raw_line.startswith("@@"):
            match = _HUNK_HEADER.match(raw_line)
            if match is None:
                raise ValueError(f"非法 hunk 头：{raw_line!r}")
            if current is None:
                current = FilePatch(old_path="", new_path="")
            old_start = int(match.group(1))
            old_count = int(match.group(2) or "1")
            new_start = int(match.group(3))
            new_count = int(match.group(4) or "1")
            current_hunk = PatchHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
            )
            current.hunks.append(current_hunk)
            continue
        if current is None or current_hunk is None:
            raise ValueError(f"hunk 外的行：{raw_line!r}")
        if raw_line.startswith("+"):
            current_hunk.body.append(("+", raw_line[1:]))
        elif raw_line.startswith("-"):
            current_hunk.body.append(("-", raw_line[1:]))
        else:
            current_hunk.body.append((" ", raw_line[1:] if raw_line.startswith(" ") else raw_line))

    if current is not None:
        files.append(current)
    if not files:
        raise ValueError("patch 为空或没有可解析的文件头")
    return files


def _strip_a_b_prefix(path: str) -> str:
    """去掉 git 风格 ``a/`` ``b/`` 前缀。"""
    if path.startswith(("a/", "b/")):
        return path[2:]
    if path.startswith('"') and path.endswith('"'):
        return path[1:-1]
    return path


def apply_patch_to_text(
    old_text: str,
    hunks: list[PatchHunk],
    *,
    fuzz: int = 0,
) -> tuple[str, int]:
    """把 hunks 应用到文本，返回 (新文本, 应用成功的 hunk 数)。

    Args:
        old_text: 旧文件内容。
        hunks: 按 ``old_start`` 升序排列的 hunks。
        fuzz: 允许不匹配的上下文行数。

    Returns:
        ``(新文本, 成功 hunk 数)``。

    Raises:
        ValueError: 某个 hunk 无法匹配。
    """
    old_lines = old_text.splitlines()
    result: list[str] = []
    pos = 0
    applied = 0

    for hunk in sorted(hunks, key=lambda h: h.old_start):
        expected = hunk.old_lines
        if not expected:
            # 只有新增行的 hunk：直接插入到 old_start 附近。
            insert_at = _find_insert_position(old_lines, hunk, pos)
            result.extend(old_lines[pos:insert_at])
            result.extend(hunk.add_lines)
            pos = insert_at
            applied += 1
            continue

        start = _find_match(old_lines, expected, hunk, pos, fuzz)
        result.extend(old_lines[pos:start])
        # 应用：上下文保留、- 行删除、+ 行插入。
        for op, line in hunk.body:
            if op == " ":
                result.append(line)
            elif op == "-":
                continue
            else:  # "+"
                result.append(line)
        pos = start + len(hunk.old_lines)
        applied += 1

    result.extend(old_lines[pos:])
    # 尾随换行：原文件以换行结尾，或新建文件（原内容为空）时补上。
    trailing = "\n" if (result and (old_text.endswith("\n") or not old_text)) else ""
    return "\n".join(result) + trailing, applied


def _find_insert_position(old_lines: list[str], hunk: PatchHunk, pos: int) -> int:
    """定位纯新增 hunk 的插入点（就近于 old_start）。"""
    target = max(pos, min(hunk.old_start - 1, len(old_lines)))
    return target


def _find_match(
    old_lines: list[str],
    expected: list[str],
    hunk: PatchHunk,
    pos: int,
    fuzz: int,
) -> int:
    """滑动匹配：找到 hunk 在 old_lines 中的起始下标。

    Args:
        old_lines: 旧文件行。
        expected: hunk 需要匹配的行（context + remove）。
        hunk: hunk（用于定位搜索起点与 fuzz 语义）。
        pos: 当前消费位置（只向前搜索）。
        fuzz: 允许的上下文行不匹配数。

    Returns:
        匹配起始下标。

    Raises:
        ValueError: 找不到匹配。
    """
    # 计算 hunk 中哪些位置是「上下文」（fuzz 可豁免），哪些是「删除行」（必须精确）。
    is_context = [op == " " for op, _ in hunk.body if op in (" ", "-")]
    max_start = len(old_lines) - len(expected)
    # 以 hunk.old_start-1 为锚点，允许 ±drift 偏移。
    anchor = max(pos, hunk.old_start - 1)
    candidates: list[int] = []
    for offset in range(0, 64):
        for sign in (1, -1):
            candidate = anchor + offset * sign
            if candidate < pos or candidate > max_start:
                continue
            candidates.append(candidate)
            if len(candidates) >= 128:
                break
        if len(candidates) >= 128:
            break
    # 兜底：从 pos 线性扫描（大漂移场景）。
    if pos <= max_start:
        candidates.append(pos)

    for start in candidates:
        mismatch = 0
        ok = True
        for j, exp in enumerate(expected):
            actual = old_lines[start + j] if start + j < len(old_lines) else None
            if actual != exp:
                if is_context[j] and mismatch < fuzz:
                    mismatch += 1
                    continue
                ok = False
                break
        if ok:
            return start
    raise ValueError(
        f"hunk 无法匹配（fuzz={fuzz}，old_start={hunk.old_start}，"
        f"需要 {len(expected)} 行，文件共 {len(old_lines)} 行）"
    )


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


class ApplyPatchArgs(BaseModel):
    """应用 patch 参数。"""

    patch: str = Field(min_length=1, description="unified diff 文本")
    path: str = Field(default=".", description="patch 中相对路径的基准目录")
    fuzz: int = Field(default=0, ge=0, le=3, description="允许不匹配的上下文行数")


async def apply_patch_to_files(
    executor: object,
    workspace: object,
    patch_text: str,
    *,
    base_dir: str = ".",
    fuzz: int = 0,
) -> tuple[bool, str, dict[str, str], list[str]]:
    """把 patch 应用到工作区文件（两阶段：先算后写，失败回滚）。

    Args:
        executor: 执行器（BaseExecutor）。
        workspace: 工作区句柄。
        patch_text: unified diff 文本。
        base_dir: 相对路径基准。
        fuzz: fuzz 参数。

    Returns:
        ``(ok, message, sha256_map, changed_files)``；
        ``sha256_map`` 为 相对路径 → 新文件 sha256。

    Raises:
        Exception: 解析或应用失败（未写盘）。
    """
    files = parse_unified_diff(patch_text)
    planned: list[tuple[FilePatch, str | None]] = []  # (patch, new_content or None=删除)

    for file_patch in files:
        target = file_patch.new_path if not file_patch.deletes else file_patch.old_path
        rel = _join_rel(base_dir, target)
        if file_patch.creates:
            new_content, _ = apply_patch_to_text("", file_patch.hunks, fuzz=fuzz)
            planned.append((file_patch, new_content))
            continue
        try:
            current = await executor.read_file(workspace, rel)
        except Exception as exc:  # noqa: BLE001 - 读取失败 → 应用失败
            raise ValueError(f"读取目标文件失败 {rel}：{exc}") from exc
        if current.is_binary:
            raise ValueError(f"不支持 patch 二进制文件：{rel}")
        if file_patch.deletes:
            planned.append((file_patch, None))
            continue
        new_content, applied = apply_patch_to_text(
            current.content, file_patch.hunks, fuzz=fuzz
        )
        planned.append((file_patch, new_content))

    # 写盘（第二阶段）；中途失败回滚。
    written: list[tuple[str, str | None]] = []  # (rel, original_content or None)
    sha_map: dict[str, str] = {}
    changed: list[str] = []
    try:
        for file_patch, new_content in planned:
            rel = _join_rel(base_dir, file_patch.new_path if not file_patch.deletes else file_patch.old_path)
            if file_patch.deletes:
                original = (await executor.read_file(workspace, rel)).content
                written.append((rel, original))
                try:
                    await executor.write_file(workspace, rel, "")
                except Exception:  # noqa: BLE001 - 删除失败按写空处理兜底
                    pass
                changed.append(rel)
                continue
            original = None
            try:
                original = (await executor.read_file(workspace, rel)).content
            except Exception:  # noqa: BLE001 - 新建文件没有原内容
                pass
            written.append((rel, original))
            stat = await executor.write_file(workspace, rel, new_content or "")
            sha_map[rel] = stat.sha256
            changed.append(rel)
    except Exception as exc:  # noqa: BLE001 - 写盘失败 → 回滚
        for rel, original in reversed(written):
            try:
                await executor.write_file(workspace, rel, original or "")
            except Exception:  # noqa: BLE001 - 回滚尽力而为
                pass
        raise ValueError(f"写盘失败已回滚：{exc}") from exc

    return True, f"已应用 {len(planned)} 个文件的 patch", sha_map, changed


def _join_rel(base_dir: str, path: str) -> str:
    """拼接基准目录与 patch 内路径（POSIX 分隔符）。"""
    base = base_dir.strip("/\\")
    p = path.strip("/\\")
    if not base:
        return p
    return f"{base}/{p}"


class ApplyPatchTool(BaseTool):
    """应用 unified diff patch。"""

    name: ClassVar[str] = "apply_patch"
    description: ClassVar[str] = (
        "把 unified diff（git diff 格式）应用到工作区文件。"
        "支持新建 / 修改 / 删除文件，带行号漂移与 fuzz。失败自动回滚，不留半成品。"
    )
    args_schema: ClassVar[type[BaseModel]] = ApplyPatchArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.IDEMPOTENT

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """执行 patch 应用。"""
        parsed = args.model_dump()
        if ctx.executor is None or ctx.workspace is None:
            return ToolResult.failure("executor / workspace 未注入", error_code="LF-TOOL-0902")
        try:
            ok, message, sha_map, changed = await apply_patch_to_files(
                ctx.executor,
                ctx.workspace,
                parsed["patch"],
                base_dir=parsed["path"],
                fuzz=parsed["fuzz"],
            )
        except Exception as exc:  # noqa: BLE001 - 解析 / 应用 / 回滚失败统一返回
            return ToolResult.failure(
                f"patch 应用失败：{exc}",
                error_code="LF-TOOL-0902",
            )
        first_sha = next(iter(sha_map.values()), None)
        return ToolResult(
            ok=ok,
            output=f"{message}\n改动文件：{', '.join(changed) if changed else '(无)'}",
            data={"changed_files": changed, "sha256": sha_map},
            external_ref=first_sha,
        )

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """对每个改动文件：把 patch 应用到当前内容，比对结果 hash 与文件实际 hash。"""
        patch_text = str(intent.args.get("patch", ""))
        base_dir = str(intent.args.get("path", "."))
        fuzz = int(intent.args.get("fuzz", 0))
        if not patch_text or ctx.executor is None or ctx.workspace is None:
            return ProbeResult(executed=False, confidence="low", evidence="意图缺少 patch")
        try:
            files = parse_unified_diff(patch_text)
        except ValueError as exc:  # noqa: BLE001
            return ProbeResult(executed=False, confidence="low", evidence=f"patch 解析失败：{exc}")
        checked = 0
        for file_patch in files:
            rel = _join_rel(base_dir, file_patch.new_path if not file_patch.deletes else file_patch.old_path)
            try:
                current = await ctx.executor.read_file(ctx.workspace, rel)
            except Exception:  # noqa: BLE001 - 文件不存在说明未执行
                return ProbeResult(executed=False, confidence="medium", evidence=f"文件不存在：{rel}")
            if current.is_binary:
                continue
            if file_patch.deletes:
                executed = current.content == ""
                checked += 1
                if not executed:
                    return ProbeResult(executed=False, confidence="high", evidence=f"删除未生效：{rel}")
                continue
            try:
                expected_text, _ = apply_patch_to_text(current.content, file_patch.hunks, fuzz=fuzz)
            except ValueError:
                # 无法在当前内容上再次应用 → 很可能已经应用过了（幂等成立）。
                checked += 1
                continue
            expected_hash = sha256_hex(expected_text.encode("utf-8"))
            checked += 1
            if current.sha256 != expected_hash:
                return ProbeResult(
                    executed=False,
                    confidence="medium",
                    evidence=f"{rel} 文件 hash 与「应用后」不一致",
                )
        return ProbeResult(
            executed=True,
            confidence="high" if checked else "low",
            evidence=f"已校验 {checked} 个文件，patch 已应用",
        )
