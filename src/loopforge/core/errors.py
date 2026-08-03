"""异常层级 —— ``LoopForgeError`` 根类 + 域异常（**签名已冻结**）。

冻结签名（``TASK_BREAKDOWN.md`` §11.2）::

    class LoopForgeError(Exception):
        code: str
        http_status: int
        details: dict[str, Any]
        retryable: bool
        def __init__(self, message: str = "", *,
                     details: Mapping[str, Any] | None = None) -> None: ...

使用约定
--------
* **禁止裸抛 ``Exception``**：所有对外可见的失败必须是 ``LoopForgeError`` 子类，
  这样 API 层（WP7）才能统一转成 ``{code, data, message, trace_id}`` 信封。
* 需要携带上下文时用 ``details``，不要把上下文拼进 ``message``：

  >>> raise TaskNotFoundError("Task 不存在", details={"task_id": "01J..."})

* 子类可以通过覆盖 ``default_code`` 精确指定错误码；也可以在实例化时用
  ``code=`` 关键字临时指定同域内的其他码。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from loopforge.core.error_codes import (
    ErrorCode,
    code_to_http,
    code_to_message,
    is_retryable,
)

__all__ = [
    "LoopForgeError",
    # 18 个域基类
    "ConfigError",
    "AuthError",
    "DatabaseError",
    "RunError",
    "TaskError",
    "LeaseError",
    "StepError",
    "ProviderError",
    "ExecutorError",
    "ToolError",
    "PolicyError",
    "BudgetError",
    "ContextError",
    "VcsError",
    "VerificationError",
    "ApiError",
    "RoleError",
    "EvolutionError",
    # 高频具体异常
    "InternalError",
    "SerializationError",
    "TimeoutError_",
    "CircuitOpenError",
    "NotFoundError",
    "UniqueViolationError",
    "OptimisticLockError",
    "RunNotFoundError",
    "TaskNotFoundError",
    "IllegalTransitionError",
    "LeaseLostError",
    "FencedOutError",
    "LeaseAcquireFailedError",
    "IdempotencyConflictError",
    "UncertainStepError",
    "ProviderAuthError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderServerError",
    "AllProvidersUnavailableError",
    "ToolNotRegisteredError",
    "PolicyDeniedError",
    "ApprovalRequiredError",
    "BudgetExhaustedError",
    "ContextOverflowError",
    "ValidationError",
    "wrap_exception",
]


class LoopForgeError(Exception):
    """全部 LoopForge 异常的根类。

    :ivar code: ``LF-<DOMAIN>-<NNNN>`` 错误码字符串。
    :ivar http_status: 由 :func:`loopforge.core.error_codes.code_to_http` 决定。
    :ivar details: 结构化上下文，会原样放进 API 响应的 ``data.details``。
    :ivar retryable: 调用方是否可以安全重试。
    """

    #: 子类通过覆盖该字段指定默认错误码。
    default_code: ErrorCode = ErrorCode.CORE_INTERNAL

    code: str
    http_status: int
    details: dict[str, Any]
    retryable: bool

    def __init__(self, message: str = "", *, details: Mapping[str, Any] | None = None) -> None:
        code = self.default_code
        self.code = str(code.value)
        self.http_status = code_to_http(code)
        self.retryable = is_retryable(code)
        self.details = dict(details) if details else {}
        self.message = message or code_to_message(code)
        super().__init__(self.message)

    # -- 表示 ---------------------------------------------------------------

    def __str__(self) -> str:
        if self.details:
            return f"[{self.code}] {self.message} details={self.details!r}"
        return f"[{self.code}] {self.message}"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, message={self.message!r}, "
            f"http_status={self.http_status}, retryable={self.retryable}, details={self.details!r})"
        )

    # -- 序列化 -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """转成可 JSON 序列化的字典（API 层与事件表共用）。"""
        return {
            "code": self.code,
            "message": self.message,
            "http_status": self.http_status,
            "retryable": self.retryable,
            "details": dict(self.details),
        }

    def with_details(self, **extra: Any) -> LoopForgeError:
        """就地追加上下文并返回自身，便于链式抛出。"""
        self.details.update(extra)
        return self


# ---------------------------------------------------------------------------
# 域基类（18 个，与 error_codes.ErrorDomain 一一对应）
# ---------------------------------------------------------------------------


class ConfigError(LoopForgeError):
    """CORE 域：配置无效。"""

    default_code = ErrorCode.CORE_INVALID_CONFIG


class AuthError(LoopForgeError):
    """AUTH 域：认证与鉴权失败。"""

    default_code = ErrorCode.AUTH_UNAUTHENTICATED


class DatabaseError(LoopForgeError):
    """DB 域：持久化失败。"""

    default_code = ErrorCode.DB_CONNECTION_FAILED


class RunError(LoopForgeError):
    """RUN 域：Run 生命周期异常。"""

    default_code = ErrorCode.RUN_ILLEGAL_TRANSITION


class TaskError(LoopForgeError):
    """TASK 域：Task 生命周期异常。"""

    default_code = ErrorCode.TASK_ILLEGAL_TRANSITION


class LeaseError(LoopForgeError):
    """LEASE 域：租约异常。"""

    default_code = ErrorCode.LEASE_ACQUIRE_FAILED


class StepError(LoopForgeError):
    """STEP 域：幂等单元异常。"""

    default_code = ErrorCode.STEP_ILLEGAL_TRANSITION


class ProviderError(LoopForgeError):
    """PROVIDER 域：模型接入异常。"""

    default_code = ErrorCode.PROVIDER_SERVER_ERROR


class ExecutorError(LoopForgeError):
    """EXECUTOR 域：执行沙箱异常。"""

    default_code = ErrorCode.EXECUTOR_NONZERO_EXIT


class ToolError(LoopForgeError):
    """TOOL 域：工具调用异常。"""

    default_code = ErrorCode.TOOL_EXECUTION_FAILED


class PolicyError(LoopForgeError):
    """POLICY 域：策略与审批异常。"""

    default_code = ErrorCode.POLICY_DENIED


class BudgetError(LoopForgeError):
    """BUDGET 域：预算异常。"""

    default_code = ErrorCode.BUDGET_EXHAUSTED


class ContextError(LoopForgeError):
    """MEMORY 域：上下文治理异常。"""

    default_code = ErrorCode.MEMORY_CONTEXT_OVERFLOW


class VcsError(LoopForgeError):
    """VCS 域：版本控制异常。"""

    default_code = ErrorCode.VCS_CLONE_FAILED


class VerificationError(LoopForgeError):
    """VERIFY 域：验证闭环异常。"""

    default_code = ErrorCode.VERIFY_UNIT_FAILED


class ApiError(LoopForgeError):
    """API 域：HTTP 接口层异常。"""

    default_code = ErrorCode.API_VALIDATION_FAILED


class RoleError(LoopForgeError):
    """ROLE 域：智能体角色异常。"""

    default_code = ErrorCode.ROLE_OUTPUT_PARSE_FAILED


class EvolutionError(LoopForgeError):
    """EVOLUTION 域：自演化异常。"""

    default_code = ErrorCode.EVOLUTION_NORMALIZE_FAILED


# ---------------------------------------------------------------------------
# 高频具体异常（跨包冻结引用）
# ---------------------------------------------------------------------------


class InternalError(LoopForgeError):
    """未归类的内部错误（``LF-CORE-0002``）。"""

    default_code = ErrorCode.CORE_INTERNAL


class SerializationError(LoopForgeError):
    """序列化 / 反序列化失败（``LF-CORE-0003``）。"""

    default_code = ErrorCode.CORE_SERIALIZATION_FAILED


class TimeoutError_(LoopForgeError):
    """内核级超时（``LF-CORE-0006``）。

    命名带下划线后缀以避免遮蔽内置 :class:`TimeoutError`。
    """

    default_code = ErrorCode.CORE_TIMEOUT


class CircuitOpenError(LoopForgeError):
    """熔断器打开（``LF-CORE-0009``）。"""

    default_code = ErrorCode.CORE_CIRCUIT_OPEN


class NotFoundError(DatabaseError):
    """记录不存在（``LF-DB-0206``）。"""

    default_code = ErrorCode.DB_NOT_FOUND


class UniqueViolationError(DatabaseError):
    """唯一约束冲突（``LF-DB-0203``）。"""

    default_code = ErrorCode.DB_UNIQUE_VIOLATION


class OptimisticLockError(DatabaseError):
    """乐观锁版本冲突（``LF-DB-0207``）。"""

    default_code = ErrorCode.DB_OPTIMISTIC_LOCK


class RunNotFoundError(RunError):
    """Run 不存在（``LF-RUN-0301``）。"""

    default_code = ErrorCode.RUN_NOT_FOUND


class TaskNotFoundError(TaskError):
    """Task 不存在（``LF-TASK-0401``）。"""

    default_code = ErrorCode.TASK_NOT_FOUND


class IllegalTransitionError(LoopForgeError):
    """非法状态迁移（``LF-RUN-0302`` / ``LF-TASK-0403``）。

    默认使用 Task 域码；Run 域调用方请抛 :class:`RunError` 的子类或在
    ``details`` 中标注 ``entity="run"``。
    """

    default_code = ErrorCode.TASK_ILLEGAL_TRANSITION


class LeaseAcquireFailedError(LeaseError):
    """租约领取失败（``LF-LEASE-0501``）。"""

    default_code = ErrorCode.LEASE_ACQUIRE_FAILED


class LeaseLostError(LeaseError):
    """租约丢失（``LF-LEASE-0502``）—— 心跳失败时抛出。"""

    default_code = ErrorCode.LEASE_LOST


class FencedOutError(LeaseError):
    """已被 Fence（``LF-LEASE-0503``）—— fence_token 过期的写操作必须拒绝。"""

    default_code = ErrorCode.LEASE_FENCED_OUT


class IdempotencyConflictError(StepError):
    """幂等键冲突（``LF-STEP-0601``）。"""

    default_code = ErrorCode.STEP_IDEMPOTENCY_CONFLICT


class UncertainStepError(StepError):
    """Step 副作用状态不确定（``LF-STEP-0602``）。"""

    default_code = ErrorCode.STEP_UNCERTAIN


class ProviderAuthError(ProviderError):
    """Provider 认证失败（``LF-PROVIDER-0701``）。"""

    default_code = ErrorCode.PROVIDER_AUTH_FAILED


class ProviderRateLimitError(ProviderError):
    """Provider 限流（``LF-PROVIDER-0702``）。"""

    default_code = ErrorCode.PROVIDER_RATE_LIMITED


class ProviderTimeoutError(ProviderError):
    """Provider 超时（``LF-PROVIDER-0703``）。"""

    default_code = ErrorCode.PROVIDER_TIMEOUT


class ProviderServerError(ProviderError):
    """Provider 服务端错误（``LF-PROVIDER-0704``）。"""

    default_code = ErrorCode.PROVIDER_SERVER_ERROR


class AllProvidersUnavailableError(ProviderError):
    """全部 Provider 不可用（``LF-PROVIDER-0705``）。"""

    default_code = ErrorCode.PROVIDER_ALL_UNAVAILABLE


class ToolNotRegisteredError(ToolError):
    """工具未注册（``LF-TOOL-0903``）。"""

    default_code = ErrorCode.TOOL_NOT_REGISTERED


class PolicyDeniedError(PolicyError):
    """策略拒绝（``LF-POLICY-1001``）。"""

    default_code = ErrorCode.POLICY_DENIED


class ApprovalRequiredError(PolicyError):
    """需要人工审批（``LF-POLICY-1002``）。"""

    default_code = ErrorCode.POLICY_APPROVAL_REQUIRED


class BudgetExhaustedError(BudgetError):
    """预算耗尽（``LF-BUDGET-1101``）。"""

    default_code = ErrorCode.BUDGET_EXHAUSTED


class ContextOverflowError(ContextError):
    """上下文超限且不可再压缩（``LF-MEMORY-1201``）。"""

    default_code = ErrorCode.MEMORY_CONTEXT_OVERFLOW


class ValidationError(ApiError):
    """请求 / 参数校验失败（``LF-API-1501``）。"""

    default_code = ErrorCode.API_VALIDATION_FAILED


def wrap_exception(
    exc: BaseException,
    *,
    message: str = "",
    details: Mapping[str, Any] | None = None,
) -> LoopForgeError:
    """把任意异常包装成 :class:`LoopForgeError`。

    已经是 ``LoopForgeError`` 的原样返回（并合并 ``details``），
    其余一律包成 :class:`InternalError` 并保留原始类型名与文本。

    :param exc: 原始异常。
    :param message: 覆盖用的人类可读消息，留空则复用原异常文本。
    :param details: 追加的结构化上下文。
    :return: 归一化后的 ``LoopForgeError``。
    """
    if isinstance(exc, LoopForgeError):
        if details:
            exc.details.update(details)
        return exc

    merged: dict[str, Any] = {"cause_type": type(exc).__name__, "cause": str(exc)}
    if details:
        merged.update(details)
    wrapped = InternalError(message or str(exc) or type(exc).__name__, details=merged)
    wrapped.__cause__ = exc
    return wrapped
