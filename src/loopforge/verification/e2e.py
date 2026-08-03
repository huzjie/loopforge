"""E2E 校验器 —— 端到端冒烟（``E2E`` 阶段）。

E2E 的探测优先级：
1. ``package.json`` 的 ``scripts.test:e2e`` / ``scripts.e2e``；
2. 存在 ``tests/e2e`` / ``e2e`` 目录且项目是 Python → ``pytest -m e2e``（目录内）；
3. 否则跳过（E2E 不是所有项目都有）。
"""

from __future__ import annotations

import json
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

__all__ = ["E2EVerifier"]


class E2EVerifier(Verifier):
    """端到端校验器。"""

    stage: ClassVar[VerificationStage] = VerificationStage.E2E

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock or get_clock()

    async def verify(self, executor: Any, workspace: Any, *, timeout_s: float = 600.0) -> VerificationResult:
        """运行 E2E 冒烟。"""
        started = self._clock.monotonic()
        try:
            listing = await executor.list_dir(workspace, ".", depth=1)
            names = {entry.name.lower() for entry in listing.entries}
        except Exception as exc:  # noqa: BLE001
            return VerificationResult(
                stage=self.stage,
                status=VerificationStatus.FAILED,
                ok=False,
                summary=f"读取项目结构失败：{exc}",
            )

        argv = await self._detect_e2e_command(executor, workspace, names)
        if argv is None:
            return VerificationResult.skipped(self.stage, "未检测到 E2E 脚本或 e2e 目录，跳过")

        try:
            result = await executor.run(make_spec(executor, workspace, argv, ".", timeout_s))
        except Exception as exc:  # noqa: BLE001
            return VerificationResult(
                stage=self.stage,
                status=VerificationStatus.FAILED,
                ok=False,
                summary=f"E2E 命令执行失败：{exc}",
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
            summary="E2E 冒烟通过" if result.ok else f"E2E 失败（{failure.value}）",
            stdout_tail=output,
            failure_class=failure,
            details={
                "argv": argv,
                "exit_code": result.exit_code,
                "hints": extract_failure_hints(stdout=result.stdout, stderr=result.stderr),
            },
        )

    async def _detect_e2e_command(
        self, executor: Any, workspace: Any, names: set[str]
    ) -> list[str] | None:
        """探测 E2E 命令。"""
        if "package.json" in names:
            try:
                content = await executor.read_file(workspace, "package.json")
                data = json.loads(content.content)
                scripts = data.get("scripts", {})
                for key in ("test:e2e", "e2e", "test:integration"):
                    if key in scripts:
                        script = str(scripts[key])
                        if script.startswith("npm run "):
                            return ["npm", "run", key]
                        return ["npm", "run", key]
            except Exception:  # noqa: BLE001 - 读取失败继续探测
                pass
        # Python：存在 e2e 目录。
        try:
            listing = await executor.list_dir(workspace, ".", depth=1)
            dirs = {entry.name.lower() for entry in listing.entries if entry.is_dir}
        except Exception:  # noqa: BLE001
            dirs = set()
        if "e2e" in dirs or "tests/e2e" in names:
            return ["pytest", "-q", "e2e"]
        return None
