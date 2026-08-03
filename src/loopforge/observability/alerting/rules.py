"""内置告警规则（10 条）与 YAML 加载。

规则字段（``config/alerts.yaml`` 与这里一一对应）::

    name:     规则名（唯一）
    metric:   监控的指标名（``loopforge_*``）
    op:       比较操作符：gt / gte / lt / lte / eq / ne
    threshold: 阈值
    duration_s: 持续秒数（去抖：需连续满足多久才触发）
    severity:  DEBUG / INFO / NOTICE / WARNING / ERROR / CRITICAL
    summary:  告警摘要模板
    labels:   附加 label（如 scope=run）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from loopforge.core.enums import Severity
from loopforge.core.errors import ConfigError

__all__ = ["AlertRule", "BUILTIN_RULES", "load_rules_from_file", "parse_rule"]

_OPS: dict[str, Any] = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
}


@dataclass(frozen=True)
class AlertRule:
    """一条告警规则。"""

    name: str
    metric: str
    op: str
    threshold: float
    duration_s: int = 0
    severity: Severity = Severity.WARNING
    summary: str = ""
    labels: dict[str, str] = field(default_factory=dict)

    def evaluate(self, value: float) -> bool:
        """判断当前值是否触发。"""
        operator = _OPS.get(self.op)
        if operator is None:
            raise ConfigError(f"未知比较操作符 {self.op!r}", details={"rule": self.name})
        return bool(operator(value, self.threshold))


def parse_rule(data: dict[str, Any]) -> AlertRule:
    """从字典构造规则（缺省字段用默认值）。"""
    severity_raw = str(data.get("severity", "WARNING")).upper()
    try:
        severity = Severity(severity_raw)
    except ValueError:
        severity = Severity.WARNING
    return AlertRule(
        name=str(data["name"]),
        metric=str(data["metric"]),
        op=str(data.get("op", "gt")),
        threshold=float(data["threshold"]),
        duration_s=int(data.get("duration_s", 0)),
        severity=severity,
        summary=str(data.get("summary", "")),
        labels=dict(data.get("labels", {})),
    )


#: 10 条内置规则 —— 与 ``config/alerts.yaml`` 保持一致。
BUILTIN_RULES: tuple[AlertRule, ...] = (
    parse_rule(
        {
            "name": "run_failed",
            "metric": "loopforge_runs_total",
            "op": "gte",
            "threshold": 1,
            "duration_s": 0,
            "severity": "WARNING",
            "summary": "存在失败的 Run",
            "labels": {"state": "FAILED"},
        }
    ),
    parse_rule(
        {
            "name": "budget_halted",
            "metric": "loopforge_budget_halt_total",
            "op": "gte",
            "threshold": 1,
            "duration_s": 0,
            "severity": "CRITICAL",
            "summary": "预算已触发停机",
        }
    ),
    parse_rule(
        {
            "name": "no_progress_detected",
            "metric": "loopforge_no_progress_total",
            "op": "gte",
            "threshold": 3,
            "duration_s": 300,
            "severity": "WARNING",
            "summary": "连续多轮无进展",
        }
    ),
    parse_rule(
        {
            "name": "llm_failover_high",
            "metric": "loopforge_llm_failover_total",
            "op": "gte",
            "threshold": 5,
            "duration_s": 600,
            "severity": "WARNING",
            "summary": "Provider 故障转移频繁",
        }
    ),
    parse_rule(
        {
            "name": "provider_error_rate",
            "metric": "loopforge_llm_calls_total",
            "op": "gte",
            "threshold": 50,
            "duration_s": 600,
            "severity": "WARNING",
            "summary": "LLM 调用错误次数过高",
            "labels": {"status": "error"},
        }
    ),
    parse_rule(
        {
            "name": "approval_starvation",
            "metric": "loopforge_approvals_pending",
            "op": "gte",
            "threshold": 10,
            "duration_s": 3600,
            "severity": "NOTICE",
            "summary": "待审批积压超过 10 条",
        }
    ),
    parse_rule(
        {
            "name": "task_attempt_failed",
            "metric": "loopforge_task_attempt_failed_total",
            "op": "gte",
            "threshold": 20,
            "duration_s": 600,
            "severity": "WARNING",
            "summary": "任务尝试失败次数过高",
        }
    ),
    parse_rule(
        {
            "name": "worker_fenced",
            "metric": "loopforge_worker_fenced_total",
            "op": "gte",
            "threshold": 3,
            "duration_s": 600,
            "severity": "WARNING",
            "summary": "Worker 被 Fence 次数过高",
        }
    ),
    parse_rule(
        {
            "name": "event_write_slow",
            "metric": "loopforge_event_write_latency_seconds",
            "op": "gte",
            "threshold": 1.0,
            "duration_s": 300,
            "severity": "NOTICE",
            "summary": "事件写入延迟超过 1 秒",
        }
    ),
    parse_rule(
        {
            "name": "db_pool_exhausted",
            "metric": "loopforge_db_pool_in_use",
            "op": "gte",
            "threshold": 25,
            "duration_s": 120,
            "severity": "CRITICAL",
            "summary": "数据库连接池接近耗尽",
        }
    ),
)


def load_rules_from_file(path: str | Path) -> list[AlertRule]:
    """从 YAML 文件加载规则。

    :raises ConfigError: 文件不存在或格式非法。
    """
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigError(f"告警规则文件不存在：{file_path}", details={"path": str(file_path)})
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"告警规则 YAML 解析失败：{exc}", details={"path": str(file_path)}) from exc
    rules = data.get("rules", []) if isinstance(data, dict) else []
    return [parse_rule(item) for item in rules if isinstance(item, dict)]
