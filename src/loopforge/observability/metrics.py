"""Prometheus 指标 —— **48 项集中声明**（``ARCHITECTURE.md`` §10.4 冻结）。

指标名与 label 集在 W0 冻结，**后续不得改名** —— 改名会让 3 个 Grafana
Dashboard 与 10 条告警规则同时失效。前缀统一 ``loopforge_``。
"""

from __future__ import annotations

from typing import Any, Final

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

__all__ = [
    "METRIC_PREFIX",
    "METRICS",
    "register_all_metrics",
    "generate_latest",
    # 具体指标（供各层打点）
    "RUNS_TOTAL",
    "RUNS_ACTIVE",
    "RUN_DURATION_SECONDS",
    "RUN_RECOVERY_TOTAL",
    "TASKS_TOTAL",
    "TASK_ATTEMPT_FAILED_TOTAL",
    "TASK_QUEUE_DEPTH",
    "TASK_WAIT_SECONDS",
    "LEASE_ACQUIRED_TOTAL",
    "LEASE_CONTENTION_TOTAL",
    "LEASE_RECLAIMED_TOTAL",
    "LEASE_ACTIVE",
    "WORKER_FENCED_TOTAL",
    "LOOP_ITERATIONS_TOTAL",
    "LOOP_DURATION_SECONDS",
    "NO_PROGRESS_TOTAL",
    "LLM_CALLS_TOTAL",
    "LLM_LATENCY_SECONDS",
    "LLM_TOKENS_TOTAL",
    "LLM_FAILOVER_TOTAL",
    "COST_USD_TOTAL",
    "COST_RATE_USD_PER_HOUR",
    "BUDGET_CONSUMED_RATIO",
    "BUDGET_HALT_TOTAL",
    "TOOL_CALLS_TOTAL",
    "TOOL_DURATION_SECONDS",
    "TOOL_OUTPUT_TRUNCATED_TOTAL",
    "POLICY_DECISIONS_TOTAL",
    "APPROVALS_PENDING",
    "APPROVAL_WAIT_SECONDS",
    "CONTEXT_TOKENS",
    "MEMORY_COMPRESSION_TOTAL",
    "CONSTRAINT_VIOLATIONS_TOTAL",
    "RETRIEVAL_LATENCY_SECONDS",
    "VERIFY_TOTAL",
    "VERIFY_DURATION_SECONDS",
    "COMMITS_TOTAL",
    "PRS_TOTAL",
    "WEBHOOK_DELIVERIES_TOTAL",
    "WORKERS_TOTAL",
    "EVENTS_WRITTEN_TOTAL",
    "EVENT_WRITE_LATENCY_SECONDS",
    "DB_POOL_IN_USE",
    "HTTP_REQUESTS_TOTAL",
    "HTTP_DURATION_SECONDS",
]

METRIC_PREFIX: Final[str] = "loopforge_"


def _c(name: str, doc: str, labelnames: tuple[str, ...] = ()) -> Counter:
    return Counter(name, doc, labelnames)


def _g(name: str, doc: str, labelnames: tuple[str, ...] = ()) -> Gauge:
    return Gauge(name, doc, labelnames)


def _h(name: str, doc: str, labelnames: tuple[str, ...] = ()) -> Histogram:
    return Histogram(name, doc, labelnames)


# -- Run ---------------------------------------------------------------------

RUNS_TOTAL: Counter = _c(f"{METRIC_PREFIX}runs_total", "累计 Run 数（按状态）", ("state",))
RUNS_ACTIVE: Gauge = _g(f"{METRIC_PREFIX}runs_active", "当前活跃 Run 数")
RUN_DURATION_SECONDS: Histogram = _h(f"{METRIC_PREFIX}run_duration_seconds", "Run 生命周期时长")
RUN_RECOVERY_TOTAL: Counter = _c(f"{METRIC_PREFIX}run_recovery_total", "Run 恢复次数", ("reason",))

# -- Task --------------------------------------------------------------------

TASKS_TOTAL: Counter = _c(f"{METRIC_PREFIX}tasks_total", "累计 Task 数（按状态）", ("state",))
TASK_ATTEMPT_FAILED_TOTAL: Counter = _c(f"{METRIC_PREFIX}task_attempt_failed_total", "Task 尝试失败次数")
TASK_QUEUE_DEPTH: Gauge = _g(f"{METRIC_PREFIX}task_queue_depth", "当前就绪队列深度")
TASK_WAIT_SECONDS: Histogram = _h(f"{METRIC_PREFIX}task_wait_seconds", "Task 排队等待时长")

# -- Lease -------------------------------------------------------------------

LEASE_ACQUIRED_TOTAL: Counter = _c(f"{METRIC_PREFIX}lease_acquired_total", "租约获取成功次数")
LEASE_CONTENTION_TOTAL: Counter = _c(f"{METRIC_PREFIX}lease_contention_total", "租约争抢失败次数")
LEASE_RECLAIMED_TOTAL: Counter = _c(f"{METRIC_PREFIX}lease_reclaimed_total", "租约回收次数", ("reason",))
LEASE_ACTIVE: Gauge = _g(f"{METRIC_PREFIX}lease_active", "当前活跃租约数")
WORKER_FENCED_TOTAL: Counter = _c(f"{METRIC_PREFIX}worker_fenced_total", "Worker 被 Fence 次数")

# -- Loop --------------------------------------------------------------------

LOOP_ITERATIONS_TOTAL: Counter = _c(f"{METRIC_PREFIX}loop_iterations_total", "Loop 迭代轮次")
LOOP_DURATION_SECONDS: Histogram = _h(f"{METRIC_PREFIX}loop_duration_seconds", "单轮 Loop 时长", ("phase",))
NO_PROGRESS_TOTAL: Counter = _c(f"{METRIC_PREFIX}no_progress_total", "无进展检测次数")

# -- LLM ---------------------------------------------------------------------

LLM_CALLS_TOTAL: Counter = _c(f"{METRIC_PREFIX}llm_calls_total", "LLM 调用次数", ("provider", "model", "status"))
LLM_LATENCY_SECONDS: Histogram = _h(f"{METRIC_PREFIX}llm_latency_seconds", "LLM 调用延迟")
LLM_TOKENS_TOTAL: Counter = _c(f"{METRIC_PREFIX}llm_tokens_total", "LLM token 用量", ("kind",))
LLM_FAILOVER_TOTAL: Counter = _c(f"{METRIC_PREFIX}llm_failover_total", "Provider 故障转移次数")

# -- Cost --------------------------------------------------------------------

COST_USD_TOTAL: Counter = _c(f"{METRIC_PREFIX}cost_usd_total", "累计成本（USD）", ("provider", "model"))
COST_RATE_USD_PER_HOUR: Gauge = _g(f"{METRIC_PREFIX}cost_rate_usd_per_hour", "成本速率（USD/小时）")
BUDGET_CONSUMED_RATIO: Gauge = _g(f"{METRIC_PREFIX}budget_consumed_ratio", "预算消耗比例", ("scope",))
BUDGET_HALT_TOTAL: Counter = _c(f"{METRIC_PREFIX}budget_halt_total", "预算停机次数")

# -- Tool --------------------------------------------------------------------

TOOL_CALLS_TOTAL: Counter = _c(f"{METRIC_PREFIX}tool_calls_total", "工具调用次数", ("tool", "status"))
TOOL_DURATION_SECONDS: Histogram = _h(f"{METRIC_PREFIX}tool_duration_seconds", "工具调用时长")
TOOL_OUTPUT_TRUNCATED_TOTAL: Counter = _c(f"{METRIC_PREFIX}tool_output_truncated_total", "工具输出截断次数")

# -- Policy ------------------------------------------------------------------

POLICY_DECISIONS_TOTAL: Counter = _c(f"{METRIC_PREFIX}policy_decisions_total", "策略决策次数", ("decision", "category"))
APPROVALS_PENDING: Gauge = _g(f"{METRIC_PREFIX}approvals_pending", "待审批数量")
APPROVAL_WAIT_SECONDS: Histogram = _h(f"{METRIC_PREFIX}approval_wait_seconds", "审批等待时长")

# -- Memory ------------------------------------------------------------------

CONTEXT_TOKENS: Gauge = _g(f"{METRIC_PREFIX}context_tokens", "上下文 token 占用", ("layer",))
MEMORY_COMPRESSION_TOTAL: Counter = _c(f"{METRIC_PREFIX}memory_compression_total", "记忆压缩次数", ("level",))
CONSTRAINT_VIOLATIONS_TOTAL: Counter = _c(f"{METRIC_PREFIX}constraint_violations_total", "约束违背次数")
RETRIEVAL_LATENCY_SECONDS: Histogram = _h(f"{METRIC_PREFIX}retrieval_latency_seconds", "检索延迟")

# -- Verify ------------------------------------------------------------------

VERIFY_TOTAL: Counter = _c(f"{METRIC_PREFIX}verify_total", "验证执行次数", ("step", "result"))
VERIFY_DURATION_SECONDS: Histogram = _h(f"{METRIC_PREFIX}verify_duration_seconds", "验证时长")

# -- VCS ---------------------------------------------------------------------

COMMITS_TOTAL: Counter = _c(f"{METRIC_PREFIX}commits_total", "提交次数")
PRS_TOTAL: Counter = _c(f"{METRIC_PREFIX}prs_total", "PR 次数", ("action",))
WEBHOOK_DELIVERIES_TOTAL: Counter = _c(f"{METRIC_PREFIX}webhook_deliveries_total", "Webhook 投递次数", ("status",))

# -- System ------------------------------------------------------------------

WORKERS_TOTAL: Gauge = _g(f"{METRIC_PREFIX}workers_total", "Worker 数", ("state",))
EVENTS_WRITTEN_TOTAL: Counter = _c(f"{METRIC_PREFIX}events_written_total", "事件写入次数")
EVENT_WRITE_LATENCY_SECONDS: Histogram = _h(f"{METRIC_PREFIX}event_write_latency_seconds", "事件写入延迟")
DB_POOL_IN_USE: Gauge = _g(f"{METRIC_PREFIX}db_pool_in_use", "数据库连接池使用数")
HTTP_REQUESTS_TOTAL: Counter = _c(f"{METRIC_PREFIX}http_requests_total", "HTTP 请求数", ("method", "path", "status"))
HTTP_DURATION_SECONDS: Histogram = _h(f"{METRIC_PREFIX}http_duration_seconds", "HTTP 请求时长", ("method", "path"))

#: 全部 48 项指标注册表（供 CI 与文档校验）。
METRICS: dict[str, Any] = {
    "runs_total": RUNS_TOTAL,
    "runs_active": RUNS_ACTIVE,
    "run_duration_seconds": RUN_DURATION_SECONDS,
    "run_recovery_total": RUN_RECOVERY_TOTAL,
    "tasks_total": TASKS_TOTAL,
    "task_attempt_failed_total": TASK_ATTEMPT_FAILED_TOTAL,
    "task_queue_depth": TASK_QUEUE_DEPTH,
    "task_wait_seconds": TASK_WAIT_SECONDS,
    "lease_acquired_total": LEASE_ACQUIRED_TOTAL,
    "lease_contention_total": LEASE_CONTENTION_TOTAL,
    "lease_reclaimed_total": LEASE_RECLAIMED_TOTAL,
    "lease_active": LEASE_ACTIVE,
    "worker_fenced_total": WORKER_FENCED_TOTAL,
    "loop_iterations_total": LOOP_ITERATIONS_TOTAL,
    "loop_duration_seconds": LOOP_DURATION_SECONDS,
    "no_progress_total": NO_PROGRESS_TOTAL,
    "llm_calls_total": LLM_CALLS_TOTAL,
    "llm_latency_seconds": LLM_LATENCY_SECONDS,
    "llm_tokens_total": LLM_TOKENS_TOTAL,
    "llm_failover_total": LLM_FAILOVER_TOTAL,
    "cost_usd_total": COST_USD_TOTAL,
    "cost_rate_usd_per_hour": COST_RATE_USD_PER_HOUR,
    "budget_consumed_ratio": BUDGET_CONSUMED_RATIO,
    "budget_halt_total": BUDGET_HALT_TOTAL,
    "tool_calls_total": TOOL_CALLS_TOTAL,
    "tool_duration_seconds": TOOL_DURATION_SECONDS,
    "tool_output_truncated_total": TOOL_OUTPUT_TRUNCATED_TOTAL,
    "policy_decisions_total": POLICY_DECISIONS_TOTAL,
    "approvals_pending": APPROVALS_PENDING,
    "approval_wait_seconds": APPROVAL_WAIT_SECONDS,
    "context_tokens": CONTEXT_TOKENS,
    "memory_compression_total": MEMORY_COMPRESSION_TOTAL,
    "constraint_violations_total": CONSTRAINT_VIOLATIONS_TOTAL,
    "retrieval_latency_seconds": RETRIEVAL_LATENCY_SECONDS,
    "verify_total": VERIFY_TOTAL,
    "verify_duration_seconds": VERIFY_DURATION_SECONDS,
    "commits_total": COMMITS_TOTAL,
    "prs_total": PRS_TOTAL,
    "webhook_deliveries_total": WEBHOOK_DELIVERIES_TOTAL,
    "workers_total": WORKERS_TOTAL,
    "events_written_total": EVENTS_WRITTEN_TOTAL,
    "event_write_latency_seconds": EVENT_WRITE_LATENCY_SECONDS,
    "db_pool_in_use": DB_POOL_IN_USE,
    "http_requests_total": HTTP_REQUESTS_TOTAL,
    "http_duration_seconds": HTTP_DURATION_SECONDS,
}

_INFO_METRIC = Info(f"{METRIC_PREFIX}build_info", "构建信息")


def register_all_metrics() -> dict[str, Any]:
    """返回全部指标注册表（幂等，无副作用）。"""
    return METRICS


def set_build_info(version: str, revision: str = "") -> None:
    """写入构建信息指标。"""
    _INFO_METRIC.info({"version": version, "revision": revision or ""})
