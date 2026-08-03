"""Patch 工具测试 —— apply_patch 解析 / 应用 / fuzz / 回滚 / probe。"""

from __future__ import annotations

from pathlib import Path

import pytest

from loopforge.executors.base import WorkspaceSpec
from loopforge.executors.local import LocalSubprocessExecutor
from loopforge.tools import StepIntent, ToolContext
from loopforge.tools.fs_read import ReadFileTool
from loopforge.tools.fs_write import WriteFileTool
from loopforge.tools.patch_apply import (
    ApplyPatchTool,
    apply_patch_to_files,
    parse_unified_diff,
)


@pytest.fixture()
async def env(tmp_path: Path) -> tuple[LocalSubprocessExecutor, object, ToolContext]:
    """执行器 + 工作区 + 上下文。"""
    executor = LocalSubprocessExecutor(workspace_root=tmp_path / "ws")
    workspace = await executor.prepare(
        WorkspaceSpec(run_id="run-patch", task_id="task-patch", kind="task")
    )
    ctx = ToolContext(
        run_id="run-patch",
        task_id="task-patch",
        step_id="step-1",
        trace_id="trace-1",
        workspace=workspace,
        executor=executor,
        idempotency_key="idem_patch",
        fence_token=1,
    )
    return executor, workspace, ctx


def test_parse_basic() -> None:
    """解析基础 unified diff。"""
    diff = """--- a/f.txt
+++ b/f.txt
@@ -1,3 +1,4 @@
 a
-b
+c
+d
"""
    files = parse_unified_diff(diff)
    assert len(files) == 1
    assert files[0].old_path == "f.txt"
    assert files[0].new_path == "f.txt"
    assert len(files[0].hunks) == 1
    assert files[0].hunks[0].remove_lines == ["b"]
    assert files[0].hunks[0].add_lines == ["c", "d"]


def test_parse_multiple_files() -> None:
    """解析多文件 patch。"""
    diff = """--- a/a.txt
+++ b/a.txt
@@ -1,1 +1,1 @@
-x
+y
--- a/b.txt
+++ b/b.txt
@@ -1,1 +1,1 @@
-p
+q
"""
    files = parse_unified_diff(diff)
    assert [f.old_path for f in files] == ["a.txt", "b.txt"]


def test_parse_new_and_deleted() -> None:
    """新建与删除文件。"""
    diff = """--- /dev/null
+++ b/new.txt
@@ -0,0 +1,1 @@
+hello
--- a/del.txt
+++ /dev/null
@@ -1,1 +0,0 @@
-gone
"""
    files = parse_unified_diff(diff)
    assert files[0].creates
    assert files[1].deletes


async def test_apply_patch_tool(env: object) -> None:
    """apply_patch 真实应用到工作区。"""
    _, _, ctx = env
    writer = WriteFileTool()
    await writer.execute(ctx, writer.args_schema(path="greet.py", content="print('hello')\n"))
    diff = """--- a/greet.py
+++ b/greet.py
@@ -1,1 +1,3 @@
 print('hello')
+print('world')
+print('!')
"""
    tool = ApplyPatchTool()
    result = await tool.execute(ctx, tool.args_schema(patch=diff))
    assert result.ok, result.output
    assert "greet.py" in result.output
    reader = ReadFileTool()
    content = await reader.execute(ctx, reader.args_schema(path="greet.py"))
    assert content.output == "print('hello')\nprint('world')\nprint('!')\n"


async def test_apply_patch_creates_file(env: object) -> None:
    """apply_patch 新建文件。"""
    _, _, ctx = env
    diff = """--- /dev/null
+++ b/readme.md
@@ -0,0 +1,2 @@
+# Title
+Body
"""
    tool = ApplyPatchTool()
    result = await tool.execute(ctx, tool.args_schema(patch=diff))
    assert result.ok
    reader = ReadFileTool()
    content = await reader.execute(ctx, reader.args_schema(path="readme.md"))
    assert content.output == "# Title\nBody\n"


async def test_apply_patch_rollback_on_bad_hunk(env: object) -> None:
    """无法匹配的 hunk → 失败且不改动文件（回滚）。"""
    _, _, ctx = env
    writer = WriteFileTool()
    await writer.execute(ctx, writer.args_schema(path="keep.txt", content="original\n"))
    diff = """--- a/keep.txt
+++ b/keep.txt
@@ -1,1 +1,1 @@
 NEVER_MATCH
+changed
"""
    tool = ApplyPatchTool()
    result = await tool.execute(ctx, tool.args_schema(patch=diff))
    assert not result.ok
    reader = ReadFileTool()
    content = await reader.execute(ctx, reader.args_schema(path="keep.txt"))
    assert content.output == "original\n"


async def test_apply_patch_fuzz(env: object) -> None:
    """fuzz 容忍上下文漂移。"""
    _, _, ctx = env
    writer = WriteFileTool()
    await writer.execute(
        ctx,
        writer.args_schema(path="fz.txt", content="XX\nB\n"),
    )
    # 上下文第一行不匹配，fuzz=1 应容忍；删除行 B 必须精确存在。
    diff = """--- a/fz.txt
+++ b/fz.txt
@@ -1,2 +1,2 @@
 AAA
-B
+C
"""
    tool = ApplyPatchTool()
    result = await tool.execute(ctx, tool.args_schema(patch=diff, fuzz=1))
    assert result.ok, result.output
    reader = ReadFileTool()
    content = await reader.execute(ctx, reader.args_schema(path="fz.txt"))
    assert "C" in content.output
    assert "B" not in content.output


async def test_apply_patch_fuzz_zero_rejects(env: object) -> None:
    """fuzz=0 时上下文不匹配必须失败。"""
    _, _, ctx = env
    writer = WriteFileTool()
    await writer.execute(
        ctx,
        writer.args_schema(path="fz2.txt", content="XX\nB\n"),
    )
    diff = """--- a/fz2.txt
+++ b/fz2.txt
@@ -1,2 +1,2 @@
 AAA
-B
+C
"""
    tool = ApplyPatchTool()
    result = await tool.execute(ctx, tool.args_schema(patch=diff, fuzz=0))
    assert not result.ok
    reader = ReadFileTool()
    content = await reader.execute(ctx, reader.args_schema(path="fz2.txt"))
    assert content.output == "XX\nB\n"


async def test_apply_patch_line_drift(env: object) -> None:
    """行号漂移：声明的 old_start 与实际位置偏差。"""
    _, _, ctx = env
    writer = WriteFileTool()
    await writer.execute(
        ctx,
        writer.args_schema(path="drift.txt", content="extra\ntarget\nend\n"),
    )
    # 声明 old_start=1 但 target 实际在第 2 行（index 1）。
    diff = """--- a/drift.txt
+++ b/drift.txt
@@ -1,3 +1,3 @@
 target
-end
+done
"""
    tool = ApplyPatchTool()
    result = await tool.execute(ctx, tool.args_schema(patch=diff))
    assert result.ok, result.output
    reader = ReadFileTool()
    content = await reader.execute(ctx, reader.args_schema(path="drift.txt"))
    assert "done" in content.output
    assert "extra" in content.output


async def test_apply_patch_probe(env: object) -> None:
    """probe 确认 patch 已应用。"""
    _, _, ctx = env
    writer = WriteFileTool()
    await writer.execute(ctx, writer.args_schema(path="probe.txt", content="a\nb\n"))
    diff = """--- a/probe.txt
+++ b/probe.txt
@@ -1,2 +1,2 @@
 a
-b
+c
"""
    tool = ApplyPatchTool()
    result = await tool.execute(ctx, tool.args_schema(patch=diff))
    assert result.ok
    intent = StepIntent(
        run_id="run-patch",
        task_id="task-patch",
        step_id="step-1",
        idempotency_key="idem_patch",
        tool_name="apply_patch",
        args={"patch": diff, "path": ".", "fuzz": 0},
    )
    probe = await tool.probe(ctx, intent)
    assert probe.executed
    assert probe.confidence == "high"


async def test_apply_patch_probe_not_executed(env: object) -> None:
    """probe 在未应用时返回 executed=False。"""
    _, _, ctx = env
    tool = ApplyPatchTool()
    diff = """--- a/never.txt
+++ b/never.txt
@@ -1,1 +1,1 @@
-x
+y
"""
    intent = StepIntent(
        run_id="run-patch",
        task_id="task-patch",
        step_id="step-1",
        idempotency_key="idem_patch",
        tool_name="apply_patch",
        args={"patch": diff, "path": ".", "fuzz": 0},
    )
    probe = await tool.probe(ctx, intent)
    assert not probe.executed
