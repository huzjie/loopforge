"""``run_tests`` 工具 —— 测试执行与结果解析（``PURE``，视为可重跑）。

框架探测
--------
* 存在 ``pyproject.toml`` / ``pytest.ini`` / ``setup.cfg``（含 pytest 节）→ pytest
* 存在 ``package.json`` 且含 ``vitest`` 依赖 → vitest
* 存在 ``package.json`` 且含 ``jest`` 依赖 → jest
* 否则回退 ``npm test``

输出解析
--------
:func:`parse_pytest_output` / :func:`parse_vitest_output` 把 stdout 解析成
结构化结果（通过数 / 失败数 / 失败列表 / 缺陷定位），供
:mod:`loopforge.verification` 复用 —— **不能只给一段 stderr 文本**。
"""

from __future__ import annotations

import re
import sys
from typing import ClassVar

from pydantic import BaseModel, Field

from loopforge.core.enums import SideEffectClass
from loopforge.tools.base import BaseTool, ProbeResult, StepIntent, ToolContext, ToolResult

__all__ = [
    "detect_framework",
    "parse_pytest_output",
    "parse_vitest_output",
    "RunTestsArgs",
    "RunTestsTool",
]

_PYTEST_SUMMARY = re.compile(
    r"^(?P<passed>\d+) passed(?:,\s*(?P<skipped>\d+) skipped)?"
    r"(?:,\s*(?P<xfailed>\d+) xfailed)?(?:,\s*(?P<xpassed>\d+) xpassed)?"
    r"(?:,\s*(?P<warnings>\d+) warnings)?"
)
_PYTEST_FAILED = re.compile(r"^(?P<count>\d+) failed")
_PYTEST_ERROR = re.compile(r"^(?P<count>\d+) error")
_PYTEST_FAILURE_LINE = re.compile(
    r"^(?P<path>.+?\.py):(?P<line>\d+):\s+in\s+(?P<test>[\w\[\]/\.\- ]+)"
)
# pytest -q 的失败摘要行：FAILED test_sample.py::test_add - assert 3 == 4
_PYTEST_QF_SUMMARY = re.compile(
    r"^FAILED\s+(?P<path>.+?\.py)::(?P<test>[\w\[\]/\.\- ]+?)\s+-\s+(?P<error>.+)$"
)
# pytest -q 的失败定位行：test_sample.py:4: AssertionError
_PYTEST_QF_LOCATION = re.compile(r"^(?P<path>.+?\.py):(?P<line>\d+):\s+(?P<error>.+)$")
_VITEST_SUMMARY = re.compile(
    r"Tests\s+(?P<failed>\d+) failed\s*\|\s*(?P<passed>\d+) passed"
    r"(?:\s*\|\s*(?P<skipped>\d+) skipped)?"
)
_VITEST_FILE_LINE = re.compile(r"^\s*❯\s+(?P<path>[^\s:]+):(?P<line>\d+):(?P<col>\d+):")


async def detect_framework(executor: object, workspace: object) -> str:
    """探测项目测试框架。

    Args:
        executor: 执行器。
        workspace: 工作区句柄。

    Returns:
        ``pytest`` / ``vitest`` / ``jest`` / ``npm``。
    """
    names: list[str] = []
    try:
        listing = await executor.list_dir(workspace, ".", depth=1)
        names = [entry.name for entry in listing.entries]
    except Exception:  # noqa: BLE001 - 探测失败回退 npm
        return "npm"

    lowered = {name.lower() for name in names}
    if "pyproject.toml" in lowered or "pytest.ini" in lowered or "setup.cfg" in lowered:
        return "pytest"
    if "package.json" in lowered:
        # 进一步读 package.json 判断 vitest / jest。
        try:
            import json

            content = await executor.read_file(workspace, "package.json")
            data = json.loads(content.content)
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "vitest" in deps:
                return "vitest"
            if "jest" in deps:
                return "jest"
        except Exception:  # noqa: BLE001 - 读取失败回退 npm
            pass
        return "npm"
    return "npm"


def parse_pytest_output(stdout: str) -> dict:
    """解析 pytest 输出为结构化结果。

    Args:
        stdout: pytest 的 stdout。

    Returns:
        ``{"passed", "failed", "skipped", "errors", "failures": [{"path","line","test"}]}``。
    """
    result: dict = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "failures": [],
    }
    for line in stdout.splitlines():
        line = line.strip()
        match = _PYTEST_SUMMARY.match(line)
        if match:
            result["passed"] = int(match.group("passed") or 0)
            result["skipped"] = int(match.group("skipped") or 0)
            continue
        match = _PYTEST_FAILED.search(line)
        if match:
            result["failed"] = int(match.group("count"))
            continue
        match = _PYTEST_ERROR.search(line)
        if match:
            result["errors"] = int(match.group("count"))
            continue
        match = _PYTEST_FAILURE_LINE.search(line)
        if match and result["failed"]:
            result["failures"].append(
                {
                    "path": match.group("path"),
                    "line": int(match.group("line")),
                    "test": match.group("test").strip(),
                }
            )
            continue
        match = _PYTEST_QF_SUMMARY.match(line)
        if match:
            result["failures"].append(
                {
                    "path": match.group("path"),
                    "line": 0,
                    "test": match.group("test").strip(),
                    "error": match.group("error").strip(),
                }
            )
            continue
        match = _PYTEST_QF_LOCATION.match(line)
        if match and result["failed"]:
            result["failures"].append(
                {
                    "path": match.group("path"),
                    "line": int(match.group("line")),
                    "test": "",
                    "error": match.group("error").strip(),
                }
            )
    return result


def parse_vitest_output(stdout: str) -> dict:
    """解析 vitest 输出为结构化结果。

    Args:
        stdout: vitest 的 stdout。

    Returns:
        ``{"passed", "failed", "skipped", "failures": [{"path","line","col"}]}``。
    """
    result: dict = {"passed": 0, "failed": 0, "skipped": 0, "failures": []}
    for line in stdout.splitlines():
        match = _VITEST_SUMMARY.search(line)
        if match:
            result["failed"] = int(match.group("failed") or 0)
            result["passed"] = int(match.group("passed") or 0)
            result["skipped"] = int(match.group("skipped") or 0)
            continue
        match = _VITEST_FILE_LINE.search(line)
        if match:
            result["failures"].append(
                {
                    "path": match.group("path"),
                    "line": int(match.group("line")),
                    "col": int(match.group("col")),
                }
            )
    return result


class RunTestsArgs(BaseModel):
    """运行测试参数。"""

    framework: str | None = Field(
        default=None, description="pytest / vitest / jest / npm；None 自动探测"
    )
    path: str = Field(default=".", description="运行目录（相对工作区根）")
    extra_args: list[str] = Field(default_factory=list, description="追加参数")
    timeout_s: float = Field(default=600.0, gt=0, le=7200)


class RunTestsTool(BaseTool):
    """自动探测并运行测试。"""

    name: ClassVar[str] = "run_tests"
    description: ClassVar[str] = (
        "自动探测项目测试框架（pytest / vitest / jest / npm test）并运行，"
        "把输出解析成结构化结果（通过数 / 失败数 / 失败用例列表）。"
    )
    args_schema: ClassVar[type[BaseModel]] = RunTestsArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.PURE

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """运行测试。"""
        parsed = args.model_dump()
        if ctx.executor is None or ctx.workspace is None:
            return ToolResult.failure("executor / workspace 未注入", error_code="LF-TOOL-0902")

        framework = parsed["framework"] or await detect_framework(ctx.executor, ctx.workspace)
        argv = _build_test_argv(framework, parsed["path"], parsed["extra_args"])
        try:
            result = await ctx.executor.run(
                _make_test_spec(ctx.executor, ctx.workspace.id, argv, parsed)
            )
        except Exception as exc:  # noqa: BLE001 - 统一转 ToolResult
            return ToolResult.failure(
                f"测试执行失败：{exc}",
                error_code=getattr(exc, "code", "LF-TOOL-0902"),
            )

        stats = _parse_for(framework, result.stdout)
        ok = result.ok and stats.get("failed", 0) == 0 and stats.get("errors", 0) == 0
        output = result.combined_output(limit=4000)
        return ToolResult(
            ok=ok,
            output=output,
            data={"framework": framework, "stats": stats, "exit_code": result.exit_code},
            truncated=result.truncated,
            bytes_out=len(output.encode("utf-8")),
        )

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """PURE：测试可重跑，视为无需探测。"""
        _ = (ctx, intent)
        return ProbeResult(
            executed=False,
            confidence="high",
            evidence="run_tests 是 PURE 工具（可重跑），无需探测",
        )


def _build_test_argv(framework: str, path: str, extra: list[str]) -> list[str]:
    """构造测试命令 argv。"""
    if framework == "pytest":
        # 用当前解释器的 -m pytest，避免 PATH 上的系统 pytest 干扰（venv 语义一致）。
        return [sys.executable, "-m", "pytest", "-q", *extra, path]
    if framework == "vitest":
        return ["npx", "vitest", "run", *extra]
    if framework == "jest":
        return ["npx", "jest", "--ci", *extra]
    return ["npm", "test", "--", *extra]


def _parse_for(framework: str, stdout: str) -> dict:
    """按框架选择解析器。"""
    if framework == "pytest":
        return parse_pytest_output(stdout)
    if framework in ("vitest", "jest"):
        return parse_vitest_output(stdout)
    return {"passed": 0, "failed": 0, "skipped": 0, "errors": 0, "failures": []}


def _make_test_spec(executor: object, handle_id: str, argv: list[str], parsed: dict) -> object:
    """构造 CommandSpec。"""
    from loopforge.executors.base import CommandSpec

    return CommandSpec(
        handle_id=handle_id,
        argv=argv,
        cwd=parsed["path"],
        timeout_s=parsed["timeout_s"],
    )
