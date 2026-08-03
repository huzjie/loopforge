"""红线用例检查器 —— 200+ 条用例的召回率自测。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``policy/redlines.py``、§7.4 DoD。

``config/redlines.yaml`` 每条用例形如::

    - id: RED-001
      description: rm -rf /
      kind: command
      argv: ["rm", "-rf", "/"]
      expected: DENY

``check_recall`` 遍历用例交给 :class:`PolicyEngine` 评估，
统计召回率（危险用例被拒的比例）与误报率（安全用例被误拒的比例）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import PolicyDecision
from loopforge.governance.policy.dsl import VALID_CATEGORIES
from loopforge.governance.policy.engine import PolicyAction, PolicyEngine, PolicyVerdict

__all__ = ["RedlineCase", "RedlineReport", "RedlineChecker", "load_redlines"]

#: kind → PolicyAction.kind 映射（PolicyAction.kind 是冻结 Literal）。
_KIND_TO_ACTION_KIND: dict[str, str] = {
    "command": "command",
    "path": "file_write",
    "network": "network",
    "git": "git",
    "secret": "secret",
    "tool": "file_read",
}


class RedlineCase(BaseModel):
    """一条红线用例。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="用例编号")
    description: str = Field(default="", description="用例说明")
    kind: str = Field(description="动作类型")
    argv: list[str] = Field(default_factory=list, description="命令参数")
    path: str | None = Field(default=None, description="路径")
    url: str | None = Field(default=None, description="URL")
    branch: str | None = Field(default=None, description="分支")
    content_sample: str | None = Field(default=None, description="内容样本")
    tool_name: str | None = Field(default=None, description="工具名")
    context: dict[str, Any] = Field(default_factory=dict, description="上下文")
    expected: PolicyDecision = Field(description="期望判定")

    def to_action(self) -> PolicyAction:
        """转成 :class:`PolicyAction`。"""
        action_kind = _KIND_TO_ACTION_KIND.get(self.kind, self.kind)
        return PolicyAction(
            kind=action_kind,  # type: ignore[arg-type]
            tool_name=self.tool_name or self.kind,
            argv=self.argv,
            path=self.path,
            url=self.url,
            branch=self.branch,
            content_sample=self.content_sample,
            context=self.context,
        )


@dataclass(slots=True)
class RedlineReport:
    """召回率报告。"""

    total: int = 0
    passed: int = 0
    recall_total: int = 0
    recall_hit: int = 0
    false_positive_total: int = 0
    false_positive_hit: int = 0
    failures: list[tuple[str, str, PolicyDecision, PolicyVerdict]] = field(default_factory=list)

    @property
    def recall_rate(self) -> float:
        """危险用例召回率（期望 DENY 的被拒比例）。"""
        if self.recall_total == 0:
            return 1.0
        return self.recall_hit / self.recall_total

    @property
    def false_positive_rate(self) -> float:
        """安全用例误报率（期望 ALLOW 的被拒比例）。"""
        if self.false_positive_total == 0:
            return 0.0
        return self.false_positive_hit / self.false_positive_total

    def to_dict(self) -> dict[str, Any]:
        """转字典。"""
        return {
            "total": self.total,
            "passed": self.passed,
            "recall_total": self.recall_total,
            "recall_hit": self.recall_hit,
            "recall_rate": self.recall_rate,
            "false_positive_total": self.false_positive_total,
            "false_positive_hit": self.false_positive_hit,
            "false_positive_rate": self.false_positive_rate,
            "failures": [
                {"id": case_id, "description": desc, "expected": expected.value, "actual": verdict.decision.value}
                for case_id, desc, expected, verdict in self.failures
            ],
        }


def load_redlines(path: Path | str | None = None) -> list[RedlineCase]:
    """加载红线用例。

    :param path: YAML 路径；``None`` 用仓库默认 ``config/redlines.yaml``。
    :returns: 用例列表。
    """
    from loopforge.core.config import project_root

    candidate = Path(path) if path is not None else project_root() / "config" / "redlines.yaml"
    if not candidate.is_file():
        raise FileNotFoundError(f"红线用例文件不存在：{candidate}")
    raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("redlines.yaml 顶层必须是列表")
    return [RedlineCase.model_validate(item) for item in raw]


class RedlineChecker:
    """红线召回率检查器。

    :param engine: 策略引擎。
    :param cases: 用例列表；``None`` 时从默认文件加载。
    """

    def __init__(
        self,
        engine: PolicyEngine,
        cases: list[RedlineCase] | None = None,
    ) -> None:
        """初始化检查器。"""
        self._engine: PolicyEngine = engine
        self._cases: list[RedlineCase] = cases if cases is not None else load_redlines()

    async def check(self) -> RedlineReport:
        """跑全部用例并统计。

        :returns: :class:`RedlineReport`。
        """
        report = RedlineReport(total=len(self._cases))
        for case in self._cases:
            verdict = await self._engine.evaluate(case.to_action())
            matched = verdict.decision is case.expected
            if case.expected is PolicyDecision.DENY:
                report.recall_total += 1
                if verdict.decision is PolicyDecision.DENY:
                    report.recall_hit += 1
                else:
                    report.failures.append((case.id, case.description, case.expected, verdict))
            elif case.expected is PolicyDecision.ALLOW:
                report.false_positive_total += 1
                if verdict.decision is not PolicyDecision.ALLOW:
                    report.false_positive_hit += 1
                    report.failures.append((case.id, case.description, case.expected, verdict))
            else:
                # REQUIRE_APPROVAL 用例只计入通过与否。
                pass
            if matched:
                report.passed += 1
        return report
