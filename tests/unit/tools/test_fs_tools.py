"""FS 工具测试 —— read_file / write_file / list_dir / search_code。"""

from __future__ import annotations

from pathlib import Path

import pytest

from loopforge.core.enums import SideEffectClass
from loopforge.executors.base import WorkspaceSpec
from loopforge.executors.local import LocalSubprocessExecutor
from loopforge.tools import ToolContext
from loopforge.tools.fs_edit import EditFileTool
from loopforge.tools.fs_list import ListDirTool
from loopforge.tools.fs_read import ReadFileTool
from loopforge.tools.fs_write import WriteFileTool
from loopforge.tools.code_search import SearchCodeTool


@pytest.fixture()
async def env(tmp_path: Path) -> tuple[LocalSubprocessExecutor, object, ToolContext]:
    """执行器 + 工作区 + 上下文。"""
    executor = LocalSubprocessExecutor(workspace_root=tmp_path / "ws")
    workspace = await executor.prepare(
        WorkspaceSpec(run_id="run-fs", task_id="task-fs", kind="task")
    )
    ctx = ToolContext(
        run_id="run-fs",
        task_id="task-fs",
        step_id="step-1",
        trace_id="trace-1",
        workspace=workspace,
        executor=executor,
        idempotency_key="idem_fs_test",
        fence_token=1,
    )
    return executor, workspace, ctx


async def test_write_then_read(env: object) -> None:
    """write_file → read_file 往返。"""
    _, _, ctx = env
    write = WriteFileTool()
    result = await write.execute(ctx, write.args_schema(path="dir/a.txt", content="hello fs"))
    assert result.ok
    assert result.external_ref  # sha256
    read = ReadFileTool()
    read_result = await read.execute(ctx, read.args_schema(path="dir/a.txt"))
    assert read_result.ok
    assert "hello fs" in read_result.output


async def test_read_missing_file(env: object) -> None:
    """读不存在的文件 → 失败结果而非异常。"""
    _, _, ctx = env
    tool = ReadFileTool()
    result = await tool.execute(ctx, tool.args_schema(path="nope.txt"))
    assert not result.ok
    assert result.error_code


async def test_list_dir_output(env: object) -> None:
    """list_dir 返回树。"""
    _, _, ctx = env
    writer = WriteFileTool()
    await writer.execute(ctx, writer.args_schema(path="a.py", content="x"))
    await writer.execute(ctx, writer.args_schema(path="sub/b.py", content="y"))
    tool = ListDirTool()
    result = await tool.execute(ctx, tool.args_schema(path=".", depth=2))
    assert result.ok
    assert "a.py" in result.output
    assert "sub" in result.output
    names = {e["name"] for e in result.data["entries"]}
    assert "a.py" in names


async def test_search_code_grep(env: object) -> None:
    """search_code grep 命中 file:line。"""
    _, _, ctx = env
    writer = WriteFileTool()
    await writer.execute(
        ctx, writer.args_schema(path="app.py", content="def foo():\n    return 42\n")
    )
    tool = SearchCodeTool()
    result = await tool.execute(ctx, tool.args_schema(query="foo"))
    assert result.ok
    assert "app.py:1" in result.output


async def test_search_code_glob(env: object) -> None:
    """search_code glob 按文件名过滤。"""
    _, _, ctx = env
    writer = WriteFileTool()
    await writer.execute(ctx, writer.args_schema(path="x.py", content="a"))
    await writer.execute(ctx, writer.args_schema(path="y.txt", content="a"))
    tool = SearchCodeTool()
    result = await tool.execute(ctx, tool.args_schema(query="a", glob="*.py"))
    assert result.ok
    assert "x.py" in result.output
    assert "y.txt" not in result.output


async def test_edit_file(env: object) -> None:
    """edit_file 局部替换。"""
    _, _, ctx = env
    writer = WriteFileTool()
    await writer.execute(ctx, writer.args_schema(path="f.txt", content="one\ntwo\n"))
    tool = EditFileTool()
    result = await tool.execute(ctx, tool.args_schema(path="f.txt", old_string="one", new_string="ONE"))
    assert result.ok
    assert result.data["replacements"] == 1
    reader = ReadFileTool()
    content = await reader.execute(ctx, reader.args_schema(path="f.txt"))
    assert content.output == "ONE\ntwo\n"


async def test_edit_file_missing_old(env: object) -> None:
    """old_string 未命中 → 失败。"""
    _, _, ctx = env
    writer = WriteFileTool()
    await writer.execute(ctx, writer.args_schema(path="f.txt", content="abc"))
    tool = EditFileTool()
    result = await tool.execute(ctx, tool.args_schema(path="f.txt", old_string="zzz", new_string="x"))
    assert not result.ok


async def test_side_effect_classes() -> None:
    """side_effect_class 冻结表。"""
    assert ReadFileTool.side_effect_class is SideEffectClass.PURE
    assert ListDirTool.side_effect_class is SideEffectClass.PURE
    assert SearchCodeTool.side_effect_class is SideEffectClass.PURE
    assert WriteFileTool.side_effect_class is SideEffectClass.IDEMPOTENT
    assert EditFileTool.side_effect_class is SideEffectClass.IDEMPOTENT


async def test_write_probe(env: object) -> None:
    """write_file 的 probe 能确认副作用。"""
    _, _, ctx = env
    tool = WriteFileTool()
    await tool.execute(ctx, tool.args_schema(path="p.txt", content="probe me"))
    from loopforge.tools import StepIntent

    intent = StepIntent(
        run_id="run-fs",
        task_id="task-fs",
        step_id="step-1",
        idempotency_key="idem_fs_test",
        tool_name="write_file",
        args={"path": "p.txt", "content": "probe me"},
    )
    probe = await tool.probe(ctx, intent)
    assert probe.executed
    assert probe.confidence == "high"
