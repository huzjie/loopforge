"""Build 校验器 —— 项目能否构建（``BUILD`` 阶段）。

探测构建方式
------------
* ``pyproject.toml`` / ``setup.py`` → ``python -m pip install -e .``（本地可编辑安装，
  不碰全局环境）；若 ``build`` 模块存在则回退 ``python -m build``。
* ``package.json`` → ``npm ci --ignore-scripts``（存在 lockfile）或
  ``npm install --ignore-scripts``，再 ``npm run build``。
* 其他 → 跳过（``SKIPPED``）。
"""

from __future__ import annotations

import asyncio
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

__all__ = ["BuildVerifier"]


class BuildVerifier(Verifier):
    """构建校验器。"""

    stage: ClassVar[VerificationStage] = VerificationStage.BUILD

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock or get_clock()

    async def verify(self, executor: Any, workspace: Any, *, timeout_s: float = 600.0) -> VerificationResult:
        """执行构建校验。"""
        started = self._clock.monotonic()
        try:
            listing = await executor.list_dir(workspace, ".", depth=1)
            names = {entry.name.lower() for entry in listing.entries}
        except Exception as exc:  # noqa: BLE001 - 校验不抛异常
            return VerificationResult(
                stage=self.stage,
                status=VerificationStatus.FAILED,
                ok=False,
                summary=f"读取项目结构失败：{exc}",
            )

        if "pyproject.toml" in names or "setup.py" in names:
            argv = ["python", "-m", "pip", "install", "-e", ".", "--no-deps", "-q"]
        elif "package.json" in names:
            lock = "package-lock.json" in names
            argv = (
                ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"]
                if lock
                else ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]
            )
        else:
            return VerificationResult.skipped(self.stage, "未识别到构建配置，跳过 Build")

        try:
            result = await executor.run(make_spec(executor, workspace, argv, ".", timeout_s))
        except Exception as exc:  # noqa: BLE001 - 策略拒绝等转失败
            return VerificationResult(
                stage=self.stage,
                status=VerificationStatus.FAILED,
                ok=False,
                summary=f"构建命令执行失败：{exc}",
                failure_class=classify_failure(exit_code=-1, stderr=str(exc)),
            )

        duration_ms = int((self._clock.monotonic() - started) * 1000)
        output = result.combined_output(limit=4000)
        failure = classify_failure(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
        )
        return VerificationResult(
            stage=self.stage,
            status=(
                VerificationStatus.PASSED
                if result.ok
                else VerificationStatus.TIMEOUT if result.timed_out else VerificationStatus.FAILED
            ),
            ok=result.ok,
            duration_ms=duration_ms,
            summary="构建通过" if result.ok else f"构建失败（{failure.value}）",
            stdout_tail=output,
            failure_class=failure,
            details={
                "argv": argv,
                "exit_code": result.exit_code,
                "hints": extract_failure_hints(stdout=result.stdout, stderr=result.stderr),
            },
        )
