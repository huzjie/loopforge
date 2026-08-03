"""验证报告聚合 —— 把多次流水线结果聚合成结构化报告。

用途：多任务（如一个 Run 里的多个 Task 各自跑验证）汇总后交给
WP4/WP6/上层 UI。输出既给机器（``to_dict`` / JSON），也给 LLM（``render_text``）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.verification.base import VerificationStage
from loopforge.verification.pipeline import PipelineReport

__all__ = ["VerificationReport", "ReportAggregator"]


class VerificationReport(BaseModel):
    """聚合报告。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    run_id: str = ""
    task_count: int = 0
    passed_tasks: int = 0
    failed_tasks: int = 0
    total_duration_ms: int = 0
    reports: list[PipelineReport] = Field(default_factory=list)
    stage_summary: dict[str, dict[str, int]] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """导出结构化字典。"""
        return {
            "ok": self.ok,
            "run_id": self.run_id,
            "task_count": self.task_count,
            "passed_tasks": self.passed_tasks,
            "failed_tasks": self.failed_tasks,
            "total_duration_ms": self.total_duration_ms,
            "stage_summary": self.stage_summary,
            "reports": [item.to_dict() for item in self.reports],
        }

    def render_text(self) -> str:
        """渲染成可读文本（喂给 LLM / 日志）。"""
        lines = [
            f"验证报告 run_id={self.run_id or '-'} ok={self.ok}",
            f"任务：{self.passed_tasks}/{self.task_count} 通过，"
            f"{self.failed_tasks} 失败，总耗时 {self.total_duration_ms}ms",
        ]
        for stage, stats in self.stage_summary.items():
            lines.append(
                f"  {stage}: 通过 {stats['passed']} / 失败 {stats['failed']} / "
                f"跳过 {stats['skipped']}"
            )
        for index, report in enumerate(self.reports, start=1):
            lines.append(f"  #{index} ok={report.ok} {report.summary}")
        return "\n".join(lines)


class ReportAggregator:
    """聚合多次流水线结果。

    Args:
        run_id: 所属 Run ID。
    """

    def __init__(self, *, run_id: str = "") -> None:
        self._run_id: str = run_id

    def aggregate(self, reports: list[PipelineReport]) -> VerificationReport:
        """聚合。

        Args:
            reports: 多次流水线结果。

        Returns:
            :class:`VerificationReport`。
        """
        stage_summary: dict[str, dict[str, int]] = {}
        for stage in VerificationStage:
            stage_summary[stage.value] = {"passed": 0, "failed": 0, "skipped": 0}

        for report in reports:
            for result in report.results:
                key = result.stage.value
                bucket = stage_summary.setdefault(
                    key, {"passed": 0, "failed": 0, "skipped": 0}
                )
                if result.status.value == "SKIPPED":
                    bucket["skipped"] += 1
                elif result.ok:
                    bucket["passed"] += 1
                else:
                    bucket["failed"] += 1

        passed = sum(1 for report in reports if report.ok)
        failed = len(reports) - passed
        return VerificationReport(
            ok=failed == 0,
            run_id=self._run_id,
            task_count=len(reports),
            passed_tasks=passed,
            failed_tasks=failed,
            total_duration_ms=sum(report.duration_ms for report in reports),
            reports=list(reports),
            stage_summary=stage_summary,
        )
