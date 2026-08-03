"""验证流水线测试 —— 在样例项目上跑出「build 通过 / 单测失败 / 定位到缺陷」。"""

from __future__ import annotations

from pathlib import Path

import pytest

from loopforge.core.enums import SideEffectClass
from loopforge.executors.base import WorkspaceSpec
from loopforge.executors.local import LocalSubprocessExecutor
from loopforge.tools import ToolContext
from loopforge.tools.fs_write import WriteFileTool
from loopforge.verification import (
    FailureClass,
    ReportAggregator,
    VerificationPipeline,
    VerificationStage,
    VerificationStatus,
    default_pipeline,
)
from loopforge.verification.detectors import classify_failure, extract_failure_hints
from loopforge.verification.failure_router import FailureRouter


@pytest.fixture()
async def env(tmp_path: Path) -> tuple[LocalSubprocessExecutor, object, ToolContext]:
    """执行器 + 工作区 + 上下文。"""
    executor = LocalSubprocessExecutor(workspace_root=tmp_path / "ws")
    workspace = await executor.prepare(
        WorkspaceSpec(run_id="run-verify", task_id="task-verify", kind="task")
    )
    ctx = ToolContext(
        run_id="run-verify",
        task_id="task-verify",
        step_id="step-1",
        trace_id="trace-1",
        workspace=workspace,
        executor=executor,
        idempotency_key="idem_verify",
        fence_token=1,
    )
    return executor, workspace, ctx


async def _seed_project(ctx: ToolContext, *, failing: bool = True) -> None:
    """写入一个最小 Python 项目。"""
    writer = WriteFileTool()
    await writer.execute(
        ctx,
        writer.args_schema(
            path="pyproject.toml",
            content="[project]\nname = \"sample\"\nversion = \"0.1.0\"\n",
        ),
    )
    await writer.execute(
        ctx,
        writer.args_schema(path="sample_lib.py", content="def add(a, b):\n    return a + b\n"),
    )
    if failing:
        test = (
            "from sample_lib import add\n\n"
            "def test_add():\n"
            "    assert add(1, 2) == 4\n"  # 缺陷：期望错误
        )
    else:
        test = (
            "from sample_lib import add\n\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )
    await writer.execute(ctx, writer.args_schema(path="test_sample.py", content=test))


async def test_pipeline_failing_project(env: object) -> None:
    """失败项目：单测失败 → failure_class=ASSERT，定位到 1 个缺陷。"""
    _, workspace, ctx = env
    await _seed_project(ctx, failing=True)

    pipeline = VerificationPipeline(
        verifiers=[v for v in default_pipeline() if v.stage.value != "E2E"]
    )
    report = await pipeline.run(ctx.executor, workspace, stages=[VerificationStage.UNIT_TEST])
    assert not report.ok
    unit = report.stage_result(VerificationStage.UNIT_TEST)
    assert unit is not None
    assert unit.status is VerificationStatus.FAILED
    assert unit.failure_class is FailureClass.ASSERT
    stats = unit.details.get("stats", {})
    assert stats.get("failed", 0) >= 1
    assert len(unit.details.get("failures", [])) >= 1


async def test_pipeline_passing_project(env: object) -> None:
    """通过项目：单测通过。"""
    _, workspace, ctx = env
    await _seed_project(ctx, failing=False)
    pipeline = VerificationPipeline(verifiers=default_pipeline())
    report = await pipeline.run(
        ctx.executor,
        workspace,
        stages=[VerificationStage.UNIT_TEST],
    )
    assert report.ok
    unit = report.stage_result(VerificationStage.UNIT_TEST)
    assert unit is not None
    assert unit.status is VerificationStatus.PASSED


async def test_pipeline_short_circuit(env: object) -> None:
    """失败后短路：后续阶段标记 SKIPPED。"""
    _, workspace, ctx = env
    await _seed_project(ctx, failing=True)
    pipeline = VerificationPipeline()
    report = await pipeline.run(ctx.executor, workspace)
    assert not report.ok
    assert report.blocking_failure is not None
    # 至少一个阶段被 SKIPPED（默认全量流水线）。
    skipped = [item for item in report.results if item.status is VerificationStatus.SKIPPED]
    assert len(skipped) >= 1


async def test_failure_router_structured(env: object) -> None:
    """FailureRouter 输出结构化修复建议（不是一段 stderr）。"""
    from loopforge.verification.base import VerificationResult

    result = VerificationResult(
        stage=VerificationStage.UNIT_TEST,
        status=VerificationStatus.FAILED,
        ok=False,
        summary="单测失败",
        stdout_tail="FAILED test_sample.py::test_add - assert 3 == 4",
        failure_class=FailureClass.ASSERT,
        details={"exit_code": 1},
    )
    router = FailureRouter()
    report = router.route(result)
    assert not report.ok
    assert report.failure_class is FailureClass.ASSERT
    assert report.fix_hint
    assert "assert" in report.fix_hint.lower() or "断言" in report.fix_hint


def test_classify_failure_matrix() -> None:
    """失败分类矩阵。"""
    assert classify_failure(exit_code=1, stderr="SyntaxError: invalid syntax") is FailureClass.COMPILE
    assert classify_failure(exit_code=1, stderr="ModuleNotFoundError: No module named x") is FailureClass.DEPENDENCY
    assert classify_failure(exit_code=1, stderr="AssertionError: assert 1 == 2") is FailureClass.ASSERT
    assert classify_failure(exit_code=1, stderr="command not found: pytest") is FailureClass.ENV
    assert classify_failure(exit_code=-9, timed_out=True) is FailureClass.TIMEOUT
    assert classify_failure(exit_code=2, stderr="unrecognized") is FailureClass.UNKNOWN


def test_extract_hints() -> None:
    """线索抽取。"""
    hints = extract_failure_hints(
        stdout="",
        stderr="line1\n  File x.py:1 in f\nAssertionError: boom\n  File y.py:2 in g\n",
    )
    assert len(hints) >= 1
    assert any("AssertionError" in h or "x.py" in h for h in hints)


async def test_report_aggregator(env: object) -> None:
    """ReportAggregator 聚合多份报告。"""
    _, workspace, ctx = env
    await _seed_project(ctx, failing=False)
    pipeline = VerificationPipeline(
        verifiers=[v for v in default_pipeline() if v.stage.value != "E2E"]
    )
    report = await pipeline.run(ctx.executor, workspace, stages=[VerificationStage.UNIT_TEST])
    agg = ReportAggregator(run_id="run-verify")
    merged = agg.aggregate([report, report])
    assert merged.task_count == 2
    assert merged.passed_tasks == 2
    assert merged.ok
    assert "UNIT_TEST" in merged.stage_summary
    text = merged.render_text()
    assert "UNIT_TEST" in text


def test_side_effect_frozen_table() -> None:
    """工具副作用分级冻结表抽查。"""
    from loopforge.tools import default_registry

    registry = default_registry()
    expected: dict[str, SideEffectClass] = {
        "read_file": SideEffectClass.PURE,
        "list_dir": SideEffectClass.PURE,
        "search_code": SideEffectClass.PURE,
        "run_tests": SideEffectClass.PURE,
        "write_file": SideEffectClass.IDEMPOTENT,
        "edit_file": SideEffectClass.IDEMPOTENT,
        "apply_patch": SideEffectClass.IDEMPOTENT,
        "git_ops": SideEffectClass.PROBEABLE,
        "create_pr": SideEffectClass.PROBEABLE,
        "http_fetch": SideEffectClass.PROBEABLE,
        "ask_human": SideEffectClass.PROBEABLE,
        "run_shell": SideEffectClass.UNSAFE,
    }
    for name, side_effect in expected.items():
        tool = registry.get(name)
        assert tool.side_effect_class is side_effect, f"{name} 分级错误"
