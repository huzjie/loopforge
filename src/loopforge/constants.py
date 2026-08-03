"""全局常量 —— 与配置项区分：**这里放不随部署环境变化的硬编码语义常量**。

会随环境变化的（超时、并发度、预算）一律走 ``core/config.py``；
会影响跨系统协议兼容性的（git trailer 键名、header 名、幂等键前缀）放这里。
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "PACKAGE_NAME",
    "DEFAULT_ENCODING",
    "ULID_LENGTH",
    "ID_COLUMN_LENGTH",
    "MONEY_PRECISION",
    "MONEY_SCALE",
    "DEFAULT_LEASE_TTL_S",
    "DEFAULT_HEARTBEAT_INTERVAL_S",
    "MAX_LEASE_TTL_S",
    "LEASE_GRACE_PERIOD_S",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_RECLAIMS",
    "DEFAULT_MAX_LOOPS",
    "DEFAULT_RETRY_ATTEMPTS",
    "DEFAULT_BACKOFF_BASE_S",
    "DEFAULT_BACKOFF_MAX_S",
    "CONTEXT_USAGE_RATIO",
    "CONTEXT_RESERVE_OUTPUT_TOKENS",
    "LAYER_TOKEN_RATIOS",
    "PINNED_MAX_RATIO",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "MAX_EVENT_EXPORT_ROWS",
    "TRAILER_PREFIX",
    "TRAILER_RUN_ID",
    "TRAILER_TASK_ID",
    "TRAILER_STEP_ID",
    "TRAILER_IDEMPOTENCY_KEY",
    "TRAILER_FENCE_TOKEN",
    "ALL_TRAILER_KEYS",
    "HEADER_TRACE_ID",
    "HEADER_IDEMPOTENCY_KEY",
    "HEADER_API_KEY",
    "HEADER_FENCE_TOKEN",
    "IDEMPOTENCY_KEY_PREFIX",
    "API_KEY_PREFIX",
    "BRANCH_PREFIX",
    "METRIC_PREFIX",
    "REQUIRED_LOG_FIELDS",
    "OPTIONAL_LOG_FIELDS",
    "TOP_LEVEL_CONFIG_SECTIONS",
    "MAX_TOOL_OUTPUT_BYTES",
    "MAX_ARTIFACT_BYTES",
    "SQLITE_BUSY_TIMEOUT_MS",
]

# ---------------------------------------------------------------------------
# 包元信息
# ---------------------------------------------------------------------------

#: 包名。
PACKAGE_NAME: Final[str] = "loopforge"

#: 全仓统一文本编码。
DEFAULT_ENCODING: Final[str] = "utf-8"

# ---------------------------------------------------------------------------
# 标识与数值精度
# ---------------------------------------------------------------------------

#: ULID 字符串长度（Crockford Base32）。
ULID_LENGTH: Final[int] = 26

#: 所有 ID 列的 VARCHAR 长度。
ID_COLUMN_LENGTH: Final[int] = 26

#: 金额精度：NUMERIC(18, 8)。
MONEY_PRECISION: Final[int] = 18
MONEY_SCALE: Final[int] = 8

# ---------------------------------------------------------------------------
# 租约与重试（默认值，可被 config 覆盖）
# ---------------------------------------------------------------------------

#: 租约默认 TTL 秒数。
DEFAULT_LEASE_TTL_S: Final[int] = 90

#: 心跳间隔秒数，约为 TTL 的 1/4 以容忍两次丢包。
DEFAULT_HEARTBEAT_INTERVAL_S: Final[int] = 20

#: 单个租约允许续到的最长 TTL。
MAX_LEASE_TTL_S: Final[int] = 1800

#: 租约过期后的宽限期，Watchdog 在此之后才回收。
LEASE_GRACE_PERIOD_S: Final[int] = 30

#: Task 默认最大尝试次数。
DEFAULT_MAX_ATTEMPTS: Final[int] = 3

#: Task 默认最大回收次数（防止无限抢占循环）。
DEFAULT_MAX_RECLAIMS: Final[int] = 5

#: Run 默认最大循环轮数。
DEFAULT_MAX_LOOPS: Final[int] = 500

#: 通用重试次数。
DEFAULT_RETRY_ATTEMPTS: Final[int] = 4

#: 指数退避基数与上限（秒）。
DEFAULT_BACKOFF_BASE_S: Final[float] = 1.0
DEFAULT_BACKOFF_MAX_S: Final[float] = 30.0

# ---------------------------------------------------------------------------
# 上下文 token 配额
# ---------------------------------------------------------------------------

#: 上下文窗口的使用率上限（留 40% 给模型自身波动与输出）。
CONTEXT_USAGE_RATIO: Final[float] = 0.60

#: 为输出预留的 token。
CONTEXT_RESERVE_OUTPUT_TOKENS: Final[int] = 8192

#: 五层记忆的 token 配额比例，五项之和为 1.0。
LAYER_TOKEN_RATIOS: Final[dict[str, float]] = {
    "PINNED": 0.08,
    "WORKING": 0.40,
    "EPISODIC": 0.22,
    "SEMANTIC": 0.20,
    "MILESTONE": 0.10,
}

#: Pinned 约束占上下文的硬上限。
PINNED_MAX_RATIO: Final[float] = 0.08

# ---------------------------------------------------------------------------
# 分页
# ---------------------------------------------------------------------------

#: 默认每页条数。
DEFAULT_PAGE_SIZE: Final[int] = 50

#: 每页条数硬上限。
MAX_PAGE_SIZE: Final[int] = 200

#: 单次事件导出的最大行数。
MAX_EVENT_EXPORT_ROWS: Final[int] = 100_000

# ---------------------------------------------------------------------------
# Git commit trailer（幂等探测凭据，§11.11 冻结）
# ---------------------------------------------------------------------------

#: trailer 键名前缀。
TRAILER_PREFIX: Final[str] = "LoopForge"

TRAILER_RUN_ID: Final[str] = "LoopForge-Run-Id"
TRAILER_TASK_ID: Final[str] = "LoopForge-Task-Id"
TRAILER_STEP_ID: Final[str] = "LoopForge-Step-Id"
TRAILER_IDEMPOTENCY_KEY: Final[str] = "LoopForge-Idempotency-Key"
TRAILER_FENCE_TOKEN: Final[str] = "LoopForge-Fence-Token"

#: 全部 trailer 键，供 commit message 解析器遍历。
ALL_TRAILER_KEYS: Final[tuple[str, ...]] = (
    TRAILER_RUN_ID,
    TRAILER_TASK_ID,
    TRAILER_STEP_ID,
    TRAILER_IDEMPOTENCY_KEY,
    TRAILER_FENCE_TOKEN,
)

# ---------------------------------------------------------------------------
# HTTP 头与前缀
# ---------------------------------------------------------------------------

HEADER_TRACE_ID: Final[str] = "X-Trace-Id"
HEADER_IDEMPOTENCY_KEY: Final[str] = "Idempotency-Key"
HEADER_API_KEY: Final[str] = "X-API-Key"
HEADER_FENCE_TOKEN: Final[str] = "X-Fence-Token"

#: Step 幂等键前缀（``core/hashing.idempotency_key`` 生成）。
IDEMPOTENCY_KEY_PREFIX: Final[str] = "idem_"

#: API Key 明文前缀。
API_KEY_PREFIX: Final[str] = "lf"

#: 自动创建分支的前缀。
BRANCH_PREFIX: Final[str] = "loopforge/"

#: Prometheus 指标统一前缀。
METRIC_PREFIX: Final[str] = "loopforge_"

# ---------------------------------------------------------------------------
# 日志与配置契约
# ---------------------------------------------------------------------------

#: 每条日志必带字段（§12.4）。
REQUIRED_LOG_FIELDS: Final[tuple[str, ...]] = ("ts", "level", "logger", "event", "trace_id")

#: 有上下文时应带的字段。
OPTIONAL_LOG_FIELDS: Final[tuple[str, ...]] = (
    "run_id",
    "task_id",
    "step_id",
    "worker_id",
    "role",
    "provider",
    "model",
)

#: ``config/default.yaml`` 的八个冻结顶层节（§12.5）。
TOP_LEVEL_CONFIG_SECTIONS: Final[tuple[str, ...]] = (
    "providers",
    "executor",
    "memory",
    "governance",
    "orchestration",
    "api",
    "worker",
    "observability",
)

# ---------------------------------------------------------------------------
# 容量上限
# ---------------------------------------------------------------------------

#: 单次工具输出写入数据库的字节上限（超出截断并置 truncated=True）。
MAX_TOOL_OUTPUT_BYTES: Final[int] = 1_048_576

#: 单个产物文件的字节上限。
MAX_ARTIFACT_BYTES: Final[int] = 64 * 1024 * 1024

#: SQLite busy_timeout（毫秒），并发写等待用。
SQLITE_BUSY_TIMEOUT_MS: Final[int] = 5000
