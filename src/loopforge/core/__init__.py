"""``loopforge.core`` —— 内核基础设施的公共导出面。

其他工作包**只应从本模块或其子模块导入**，不要跨层直接 import 私有名字。

模块地图::

    enums.py        11 组冻结枚举
    error_codes.py  18 个域的错误码注册表
    errors.py       LoopForgeError 体系
    event_types.py  72 种 EventType + payload 模型
    event_bus.py    进程内异步事件总线
    config.py       七层覆盖配置树
    logging.py      structlog 结构化日志
    clock.py        Clock 协议（全仓禁用 datetime.now()）
    ids.py          ULID / trace_id 生成
    hashing.py      canonical_json / sha256_hex / chain_hash
    redaction.py    24 条 Secret 正则脱敏
    resilience.py   重试 / 熔断 / 限流 / 超时
    registry.py     泛型 Registry[T]
"""

from __future__ import annotations

from loopforge.core.clock import (
    Clock,
    FrozenClock,
    OffsetClock,
    SystemClock,
    ensure_utc,
    from_iso8601,
    get_clock,
    set_clock,
    to_iso8601,
)
from loopforge.core.config import (
    Settings,
    get_settings,
    load_settings,
    reload_settings,
    set_settings,
)
from loopforge.core.enums import (
    ACTIVE_RUN_STATES,
    ALL_ENUMS,
    SEVERITY_ORDER,
    TERMINAL_RUN_STATES,
    TERMINAL_TASK_STATES,
    BudgetAction,
    BudgetScope,
    LeaseState,
    MemoryLayer,
    PolicyDecision,
    RoleName,
    RunState,
    Severity,
    SideEffectClass,
    StepState,
    TaskState,
)
from loopforge.core.error_codes import (
    ERROR_REGISTRY,
    OK_CODE,
    ErrorCode,
    ErrorDomain,
    ErrorSpec,
    all_codes,
    code_to_http,
    code_to_message,
    domain_of,
    is_retryable,
    spec_of,
)
from loopforge.core.errors import (
    ApiError,
    ApprovalRequiredError,
    AuthError,
    BudgetError,
    BudgetExhaustedError,
    CircuitOpenError,
    ConfigError,
    ContextError,
    ContextOverflowError,
    DatabaseError,
    EvolutionError,
    ExecutorError,
    FencedOutError,
    IdempotencyConflictError,
    IllegalTransitionError,
    InternalError,
    LeaseError,
    LeaseLostError,
    LoopForgeError,
    NotFoundError,
    OptimisticLockError,
    PolicyDeniedError,
    PolicyError,
    ProviderError,
    RoleError,
    RunError,
    RunNotFoundError,
    StepError,
    TaskError,
    TaskNotFoundError,
    TimeoutError_,
    ToolError,
    UncertainStepError,
    UniqueViolationError,
    ValidationError,
    VcsError,
    VerificationError,
    wrap_exception,
)
from loopforge.core.event_bus import EventBus, EventEnvelope, get_event_bus, set_event_bus
from loopforge.core.event_types import (
    EVENT_GROUPS,
    EVENT_PAYLOAD_MAP,
    EVENT_TYPE_COUNT,
    BaseEventPayload,
    EventType,
    payload_model_for,
    validate_payload,
)
from loopforge.core.hashing import (
    GENESIS_HASH,
    canonical_json,
    chain_hash,
    content_hash,
    idempotency_key,
    sha256_hex,
)
from loopforge.core.ids import is_ulid, new_trace_id, new_ulid, short_id
from loopforge.core.logging import bind_context, configure_logging, get_logger, log_context
from loopforge.core.redaction import redact_mapping, redact_text
from loopforge.core.registry import Registry
from loopforge.core.resilience import (
    CircuitBreaker,
    TokenBucketLimiter,
    retry_async,
    timeout_guard,
)

__all__ = [
    # clock
    "Clock",
    "FrozenClock",
    "OffsetClock",
    "SystemClock",
    "ensure_utc",
    "from_iso8601",
    "get_clock",
    "set_clock",
    "to_iso8601",
    # config
    "Settings",
    "get_settings",
    "load_settings",
    "reload_settings",
    "set_settings",
    # enums
    "ACTIVE_RUN_STATES",
    "ALL_ENUMS",
    "SEVERITY_ORDER",
    "TERMINAL_RUN_STATES",
    "TERMINAL_TASK_STATES",
    "BudgetAction",
    "BudgetScope",
    "LeaseState",
    "MemoryLayer",
    "PolicyDecision",
    "RoleName",
    "RunState",
    "Severity",
    "SideEffectClass",
    "StepState",
    "TaskState",
    # error codes
    "ERROR_REGISTRY",
    "OK_CODE",
    "ErrorCode",
    "ErrorDomain",
    "ErrorSpec",
    "all_codes",
    "code_to_http",
    "code_to_message",
    "domain_of",
    "is_retryable",
    "spec_of",
    # errors
    "ApiError",
    "ApprovalRequiredError",
    "AuthError",
    "BudgetError",
    "BudgetExhaustedError",
    "CircuitOpenError",
    "ConfigError",
    "ContextError",
    "ContextOverflowError",
    "DatabaseError",
    "EvolutionError",
    "ExecutorError",
    "FencedOutError",
    "IdempotencyConflictError",
    "IllegalTransitionError",
    "InternalError",
    "LeaseError",
    "LeaseLostError",
    "LoopForgeError",
    "NotFoundError",
    "OptimisticLockError",
    "PolicyDeniedError",
    "PolicyError",
    "ProviderError",
    "RoleError",
    "RunError",
    "RunNotFoundError",
    "StepError",
    "TaskError",
    "TaskNotFoundError",
    "TimeoutError_",
    "ToolError",
    "UncertainStepError",
    "UniqueViolationError",
    "ValidationError",
    "VcsError",
    "VerificationError",
    "wrap_exception",
    # event bus & types
    "EVENT_GROUPS",
    "EVENT_PAYLOAD_MAP",
    "EVENT_TYPE_COUNT",
    "BaseEventPayload",
    "EventBus",
    "EventEnvelope",
    "EventType",
    "get_event_bus",
    "payload_model_for",
    "set_event_bus",
    "validate_payload",
    # hashing
    "GENESIS_HASH",
    "canonical_json",
    "chain_hash",
    "content_hash",
    "idempotency_key",
    "sha256_hex",
    # ids
    "is_ulid",
    "new_trace_id",
    "new_ulid",
    "short_id",
    # logging
    "bind_context",
    "configure_logging",
    "get_logger",
    "log_context",
    # redaction
    "redact_mapping",
    "redact_text",
    # registry & resilience
    "CircuitBreaker",
    "Registry",
    "TokenBucketLimiter",
    "retry_async",
    "timeout_guard",
]
