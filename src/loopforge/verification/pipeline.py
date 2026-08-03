"""验证流水线 —— 自测闭环主入口（Build → Unit Test → E2E → Lint → Lifecycle）。

对应 Qwen3.8 Self-testing 范式。:class:`VerificationPipeline` 负责：

1. 按顺序执行各阶段校验器（可配置启用 / 跳过）；
2. 任一阶段失败默认**短路**（后续阶段标记 SKIPPED）；
3. 汇总 :class:`PipelineReport`，并把失败经
   :class:`~loopforge.verification.failure_router.FailureRouter` 转成
   结构化修复建议。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.clock import Clock, get_clock
from loopforge.core.logging import get_logger
from loopforge.verification.base import (
    FailureClass,
    VerificationResult,
    VerificationStage,
    VerificationStatus,
    Verifier,
)
from loopforge.verification.build import BuildVerifier
from loopforge.verification.e2e import E2EVerifier
from loopforge.verification.failure_router import FailureReport, FailureRouter
from loopforge.verification.lifecycle import LifecycleVerifier
from loopforge.verification.lint import LintVerifier
from loopforge.verification.unit_test import UnitTestVerifier

__all__ = ["PipelineReport", "VerificationPipeline", "default_pipeline"]

_log = get_logger(__name__)


class PipelineReport(BaseModel):
    """一次流水线运行的结果。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool = False
    duration_ms: int = 0
    results: list[VerificationResult] = Field(default_factory=list)
    blocking_failure: FailureReport | None = None
    summary: str = ""

    def stage_result(self, stage: VerificationStage) -> VerificationResult | None:
        """按阶段取结果。"""
        for item in self.results:
            if item.stage is stage:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        """导出结构化字典。"""
        return {
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "stages": [item.to_dict() for item in self.results],
            "blocking_failure": self.blocking_failure.to_dict() if self.blocking_failure else None,
        }


class VerificationPipeline:
    """验证流水线。

    Args:
        verifiers: 有序校验器列表；默认 Build → Unit → E2E → Lint → Lifecycle。
        stop_on_failure: 失败后是否短路后续阶段。
        clock: 时间源。
        router: 失败路由器。
    """

    def __init__(
        self,
        *,
        verifiers: list[Verifier] | None = None,
        stop_on_failure: bool = True,
        clock: Clock | None = None,
        router: FailureRouter | None = None,
    ) -> None:
        self._verifiers: list[Verifier] = verifiers or default_pipeline()
        self._stop_on_failure: bool = stop_on_failure
        self._clock: Clock = clock or get_clock()
        self._router: FailureRouter = router or FailureRouter()

    @property
    def verifiers(self) -> list[Verifier]:
        """当前校验器列表。"""
        return self._verifiers

    async def run(
        self,
        executor: Any,
        workspace: Any,
        *,
        stages: list[VerificationStage] | None = None,
        timeout_s: float = 600.0,
    ) -> PipelineReport:
        """运行流水线。

        Args:
            executor: 执行器。
            workspace: 工作区句柄。
            stages: 只运行这些阶段（顺序保持）；``None`` 运行全部。
            timeout_s: 单阶段超时。

        Returns:
            :class:`PipelineReport`。
        """
        started = self._clock.monotonic()
        wanted = {item.value for item in stages} if stages else None
        results: list[VerificationResult] = []
        blocked = False

        for verifier in self._verifiers:
            if wanted is not None and verifier.stage.value not in wanted:
                continue
            if blocked and self._stop_on_failure:
                results.append(VerificationResult.skipped(verifier.stage, "前置阶段失败，跳过"))
                continue
            _log.info("verify_stage_start", stage=verifier.stage.value)
            try:
                result = await verifier.verify(executor, workspace, timeout_s=timeout_s)
            except Exception as exc:  # noqa: BLE001 - 校验器本身异常也转成失败
                result = VerificationResult(
                    stage=verifier.stage,
                    status=VerificationStatus.FAILED,
                    ok=False,
                    summary=f"校验器异常：{exc}",
                    failure_class=FailureClass.UNKNOWN,
                )
            results.append(result)
            if not result.ok:
                blocked = True

        duration_ms = int((self._clock.monotonic() - started) * 1000)
        blocking = self._router.first_blocking(results)
        ok = all(item.ok for item in results)
        summary = "验证全部通过" if ok else (
            f"验证失败：首个阻塞阶段 {blocking.stage.value if blocking else '?'} "
            f"（{blocking.failure_class.value if blocking else '?'}）"
        )
        return PipelineReport(
            ok=ok,
            duration_ms=duration_ms,
            results=results,
            blocking_failure=blocking,
            summary=summary,
        )


def default_pipeline() -> list[Verifier]:
    """构造默认校验器序列。"""
    return [
        BuildVerifier(),
        UnitTestVerifier(),
        E2EVerifier(),
        LintVerifier(),
        LifecycleVerifier(),
    ]
