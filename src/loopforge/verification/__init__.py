"""``verification`` 包 —— 自测闭环。

流水线顺序（Qwen3.8 Self-testing）：Build → Unit Test → E2E → Lint → Lifecycle。
失败一律结构化（:class:`FailureClass`），经
:class:`~loopforge.verification.failure_router.FailureRouter` 转成修复子任务输入。
"""

from __future__ import annotations

from loopforge.verification.base import (  # noqa: F401
    FailureClass,
    VerificationResult,
    VerificationStage,
    VerificationStatus,
    Verifier,
)
from loopforge.verification.build import BuildVerifier  # noqa: F401
from loopforge.verification.e2e import E2EVerifier  # noqa: F401
from loopforge.verification.failure_router import (  # noqa: F401
    FailureReport,
    FailureRouter,
)
from loopforge.verification.lifecycle import LifecycleVerifier  # noqa: F401
from loopforge.verification.lint import LintVerifier  # noqa: F401
from loopforge.verification.pipeline import (  # noqa: F401
    PipelineReport,
    VerificationPipeline,
    default_pipeline,
)
from loopforge.verification.report import (  # noqa: F401
    ReportAggregator,
    VerificationReport,
)
from loopforge.verification.unit_test import UnitTestVerifier  # noqa: F401

__all__ = [
    "FailureClass",
    "VerificationStage",
    "VerificationStatus",
    "VerificationResult",
    "Verifier",
    "BuildVerifier",
    "UnitTestVerifier",
    "E2EVerifier",
    "LintVerifier",
    "LifecycleVerifier",
    "FailureRouter",
    "FailureReport",
    "VerificationPipeline",
    "PipelineReport",
    "default_pipeline",
    "ReportAggregator",
    "VerificationReport",
]
