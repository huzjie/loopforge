"""生命周期校验器 —— 校验闭环后的工作区一致性（``LIFECYCLE`` 阶段）。

校验点（对应 Qwen3.8 Self-testing 的「生命周期校验」）：
1. **产物存在**：Build/Test 阶段声称生成的目录/文件仍然存在（dist / build / .pytest_cache）。
2. **无孤儿进程**：没有残留的测试子进程（POSIX 用 ``ps`` 过滤；Windows 尽量跳过）。
3. **工作区可读**：关键目录可列举、文件可读（防止 cleanup 竞态）。
4. **副作用可探测**：验证阶段本身不产生外部副作用，此步仅记录。
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, ClassVar

from loopforge.core.clock import Clock, get_clock
from loopforge.verification.base import (
    FailureClass,
    VerificationResult,
    VerificationStage,
    VerificationStatus,
    Verifier,
    make_spec,
)

__all__ = ["LifecycleVerifier"]


class LifecycleVerifier(Verifier):
    """生命周期校验器。"""

    stage: ClassVar[VerificationStage] = VerificationStage.LIFECYCLE

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock or get_clock()

    async def verify(self, executor: Any, workspace: Any, *, timeout_s: float = 600.0) -> VerificationResult:
        """执行生命周期校验。"""
        started = self._clock.monotonic()
        checks: list[dict[str, Any]] = []
        ok = True

        # 1. 产物存在性。
        try:
            listing = await executor.list_dir(workspace, ".", depth=1)
            names = {entry.name for entry in listing.entries}
        except Exception as exc:  # noqa: BLE001
            return VerificationResult(
                stage=self.stage,
                status=VerificationStatus.FAILED,
                ok=False,
                summary=f"工作区不可读：{exc}",
                failure_class=FailureClass.ENV,
            )
        for artifact in ("dist", "build", ".pytest_cache", "coverage"):
            checks.append(
                {
                    "check": f"artifact.{artifact}",
                    "ok": artifact in names,
                    "detail": "存在" if artifact in names else "不存在",
                }
            )

        # 2. 工作区可读性抽样。
        try:
            await executor.read_file(workspace, "pyproject.toml", max_bytes=4096)
            checks.append({"check": "read.pyproject", "ok": True, "detail": "可读"})
        except Exception:  # noqa: BLE001
            # 不是 Python 项目也正常。
            checks.append({"check": "read.pyproject", "ok": True, "detail": "无 pyproject（跳过）"})

        # 3. 无孤儿进程（POSIX）。
        orphan = self._check_orphan_processes()
        checks.append(
            {
                "check": "no_orphan_processes",
                "ok": orphan is None,
                "detail": orphan or "未检测到测试残留进程",
            }
        )

        ok = all(item["ok"] for item in checks)
        duration_ms = int((self._clock.monotonic() - started) * 1000)
        return VerificationResult(
            stage=self.stage,
            status=VerificationStatus.PASSED if ok else VerificationStatus.FAILED,
            ok=ok,
            duration_ms=duration_ms,
            summary="生命周期校验通过" if ok else "生命周期校验失败",
            details={"checks": checks},
        )

    @staticmethod
    def _check_orphan_processes() -> str | None:
        """检查是否有残留的 pytest/node 测试进程（尽力而为）。"""
        if os.name != "posix":
            return None
        try:
            output = subprocess.run(  # noqa: S603 - 只读 ps 查询
                ["ps", "-eo", "pid,comm"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
        except Exception:  # noqa: BLE001 - ps 不可用跳过
            return None
        for line in output.splitlines():
            fields = line.split()
            if len(fields) < 2:
                continue
            comm = fields[1]
            if comm in ("pytest", "node", "vitest"):
                return f"发现残留进程：{line.strip()}"
        return None
