"""Lint 校验器 —— 静态检查（``LINT`` 阶段）。

探测优先级：ruff → mypy → eslint → 跳过。任一工具存在即运行；多个存在时
逐个运行，全部通过才算过。
"""

from __future__ import annotations

from typing import Any, ClassVar

from loopforge.core.clock import Clock, get_clock
from loopforge.verification.base import (
    VerificationResult,
    VerificationStage,
    VerificationStatus,
    Verifier,
    make_spec,
)
from loopforge.verification.detectors import classify_failure, extract_failure_hints

__all__ = ["LintVerifier"]

#: 按优先级排列的 lint 候选命令。
_LINT_CANDIDATES: tuple[tuple[str, list[str]], ...] = (
    ("ruff", ["ruff", "check", "."]),
    ("mypy", ["mypy", "src"]),
    ("eslint", ["npx", "eslint", "."]),
)


class LintVerifier(Verifier):
    """静态检查校验器。"""

    stage: ClassVar[VerificationStage] = VerificationStage.LINT

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock or get_clock()

    async def verify(self, executor: Any, workspace: Any, *, timeout_s: float = 600.0) -> VerificationResult:
        """运行可用的 lint 工具。"""
        started = self._clock.monotonic()
        executed: list[str] = []
        failures: list[VerificationResult] = []

        for tool_name, argv in _LINT_CANDIDATES:
            try:
                probe = await executor.run(
                    make_spec(executor, workspace, [argv[0], "--version"], ".", 30.0)
                )
            except Exception:  # noqa: BLE001 - 命令不可用跳过
                continue
            if not probe.ok and "not found" not in probe.stderr.lower():
                continue
            if "not found" in probe.stderr.lower() or "not recognized" in probe.stderr.lower():
                continue
            executed.append(tool_name)
            try:
                result = await executor.run(make_spec(executor, workspace, argv, ".", timeout_s))
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    VerificationResult(
                        stage=self.stage,
                        status=VerificationStatus.FAILED,
                        ok=False,
                        summary=f"{tool_name} 执行失败：{exc}",
                        failure_class=classify_failure(exit_code=-1, stderr=str(exc)),
                    )
                )
                continue
            if not result.ok:
                failure = classify_failure(
                    exit_code=result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    timed_out=result.timed_out,
                )
                failures.append(
                    VerificationResult(
                        stage=self.stage,
                        status=VerificationStatus.FAILED,
                        ok=False,
                        summary=f"{tool_name} 未通过（{failure.value}）",
                        stdout_tail=result.combined_output(limit=4000),
                        failure_class=failure,
                        details={
                            "tool": tool_name,
                            "exit_code": result.exit_code,
                            "hints": extract_failure_hints(stdout=result.stdout, stderr=result.stderr),
                        },
                    )
                )

        duration_ms = int((self._clock.monotonic() - started) * 1000)
        if not executed:
            return VerificationResult.skipped(self.stage, "未检测到 ruff/mypy/eslint，跳过 Lint")
        if failures:
            first = failures[0]
            return VerificationResult(
                stage=self.stage,
                status=VerificationStatus.FAILED,
                ok=False,
                duration_ms=duration_ms,
                summary=f"Lint 失败（{first.failure_class.value}）：{first.summary}",
                stdout_tail=first.stdout_tail,
                failure_class=first.failure_class,
                details={"tools": executed, "failures": [f.to_dict() for f in failures]},
            )
        return VerificationResult(
            stage=self.stage,
            status=VerificationStatus.PASSED,
            ok=True,
            duration_ms=duration_ms,
            summary=f"Lint 通过（{', '.join(executed)}）",
            details={"tools": executed},
        )
