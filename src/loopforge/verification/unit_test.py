"""Unit Test 校验器 —— 运行单测并解析为结构化结果（``UNIT_TEST`` 阶段）。

复用 :mod:`loopforge.tools.test_runner` 的框架探测与输出解析，保证工具层与
验证层对「测试通过/失败」的理解完全一致。
"""

from __future__ import annotations

from typing import Any, ClassVar

from loopforge.core.clock import Clock, get_clock
from loopforge.tools.test_runner import _build_test_argv, _parse_for, detect_framework
from loopforge.verification.base import (
    VerificationResult,
    VerificationStage,
    VerificationStatus,
    Verifier,
    make_spec,
)
from loopforge.verification.detectors import classify_failure, extract_failure_hints

__all__ = ["UnitTestVerifier"]


class UnitTestVerifier(Verifier):
    """单元测试校验器。"""

    stage: ClassVar[VerificationStage] = VerificationStage.UNIT_TEST

    def __init__(self, *, clock: Clock | None = None, framework: str | None = None) -> None:
        self._clock: Clock = clock or get_clock()
        self._framework: str | None = framework

    async def verify(self, executor: Any, workspace: Any, *, timeout_s: float = 600.0) -> VerificationResult:
        """运行单测并解析。"""
        started = self._clock.monotonic()
        framework = self._framework or await detect_framework(executor, workspace)
        argv = _build_test_argv(framework, ".", [])
        try:
            result = await executor.run(make_spec(executor, workspace, argv, ".", timeout_s))
        except Exception as exc:  # noqa: BLE001 - 校验不抛异常
            return VerificationResult(
                stage=self.stage,
                status=VerificationStatus.FAILED,
                ok=False,
                summary=f"测试命令执行失败：{exc}",
                failure_class=classify_failure(exit_code=-1, stderr=str(exc)),
            )

        stats = _parse_for(framework, result.stdout)
        duration_ms = int((self._clock.monotonic() - started) * 1000)
        output = result.combined_output(limit=4000)
        ok = result.ok and stats.get("failed", 0) == 0 and stats.get("errors", 0) == 0
        failure = classify_failure(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
        )
        if ok:
            summary = (
                f"单测通过：passed={stats.get('passed', 0)} "
                f"skipped={stats.get('skipped', 0)}"
            )
        else:
            summary = (
                f"单测失败：failed={stats.get('failed', 0)} "
                f"errors={stats.get('errors', 0)} passed={stats.get('passed', 0)}"
            )
        return VerificationResult(
            stage=self.stage,
            status=(
                VerificationStatus.PASSED
                if ok
                else VerificationStatus.TIMEOUT if result.timed_out else VerificationStatus.FAILED
            ),
            ok=ok,
            duration_ms=duration_ms,
            summary=summary,
            stdout_tail=output,
            failure_class=failure,
            details={
                "framework": framework,
                "stats": stats,
                "exit_code": result.exit_code,
                "failures": stats.get("failures", []),
                "hints": extract_failure_hints(stdout=result.stdout, stderr=result.stderr),
            },
        )
