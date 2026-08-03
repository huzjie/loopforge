"""``loopforge.core.errors`` + ``loopforge.core.error_codes`` 单测。

错误码是**冻结公共词汇**，下游 7 个 WP 与前端都按 ``LF-<DOMAIN>-<NNNN>``
硬编码分支，因此这里的断言等价于契约测试：

* 码格式、域范围、唯一性；
* 每个码都有 HTTP 映射、人类消息、可重试标记；
* 18 个域基类与 ``ErrorDomain`` 一一对应；
* 具体异常继承链正确（``except LeaseError`` 能抓到 ``FencedOutError``）；
* ``to_dict()`` 可 JSON 化，供 API 与 events 表共用；
* ``wrap_exception`` 的幂等与因果链保留。
"""

from __future__ import annotations

import json
import re

import pytest

from loopforge.core.error_codes import (
    DOMAIN_RANGES,
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
    AllProvidersUnavailableError,
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
    LeaseAcquireFailedError,
    LeaseError,
    LeaseLostError,
    LoopForgeError,
    NotFoundError,
    OptimisticLockError,
    PolicyDeniedError,
    PolicyError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderServerError,
    ProviderTimeoutError,
    RoleError,
    RunError,
    RunNotFoundError,
    SerializationError,
    StepError,
    TaskError,
    TaskNotFoundError,
    TimeoutError_,
    ToolError,
    ToolNotRegisteredError,
    UncertainStepError,
    UniqueViolationError,
    ValidationError,
    VcsError,
    VerificationError,
    wrap_exception,
)

CODE_RE = re.compile(r"^LF-[A-Z]+-\d{4}$")

#: 18 个域基类 ↔ ErrorDomain 的对照表（键即 ``LF-<DOMAIN>-`` 中的 DOMAIN 段）。
DOMAIN_BASE_CLASSES: dict[str, type[LoopForgeError]] = {
    "CORE": ConfigError,
    "AUTH": AuthError,
    "DB": DatabaseError,
    "RUN": RunError,
    "TASK": TaskError,
    "LEASE": LeaseError,
    "STEP": StepError,
    "PROVIDER": ProviderError,
    "EXECUTOR": ExecutorError,
    "TOOL": ToolError,
    "POLICY": PolicyError,
    "BUDGET": BudgetError,
    "MEMORY": ContextError,
    "VCS": VcsError,
    "VERIFY": VerificationError,
    "API": ApiError,
    "ROLE": RoleError,
    "EVOLUTION": EvolutionError,
}


# ---------------------------------------------------------------------------
# 错误码本体
# ---------------------------------------------------------------------------


def test_all_codes_match_format() -> None:
    """每个错误码都必须是 ``LF-<DOMAIN>-<NNNN>``。"""
    codes = all_codes()
    assert codes, "错误码表不能为空"
    for code in codes:
        assert CODE_RE.match(str(code.value)), f"错误码格式非法：{code.value}"


def test_codes_are_unique() -> None:
    """码值全局唯一 —— 重复会让前端分支错乱。"""
    values = [str(code.value) for code in ErrorCode]
    assert len(values) == len(set(values))


def test_ok_code_is_special() -> None:
    """``OK_CODE`` 是成功哨兵，不参与错误分支。"""
    assert OK_CODE == "OK"
    assert OK_CODE not in {str(code.value) for code in ErrorCode}


def test_registry_covers_every_code() -> None:
    """每个码都要在注册表里有 :class:`ErrorSpec`。"""
    assert len(ERROR_REGISTRY) == len(ErrorCode)
    for code in ErrorCode:
        spec = ERROR_REGISTRY[code]
        assert isinstance(spec, ErrorSpec)
        assert spec.message_zh.strip(), f"{code.value} 缺少人类可读消息"
        assert 400 <= spec.http_status <= 599


def test_domain_ranges_are_declared_for_all_domains() -> None:
    """18 个域全部声明了编号区间，且区间不重叠。"""
    assert len(ErrorDomain) == 18
    assert set(DOMAIN_RANGES) == set(ErrorDomain)
    spans = sorted(DOMAIN_RANGES.values())
    for (low, high), (next_low, _) in zip(spans, spans[1:], strict=False):
        assert low <= high
        assert high < next_low, f"域区间重叠：{(low, high)} 与 {next_low}"


def test_code_number_falls_into_its_domain_range() -> None:
    """码的数字部分必须落在所属域的区间内。"""
    for code in ErrorCode:
        domain = domain_of(code)
        low, high = DOMAIN_RANGES[domain]
        number = spec_of(code).number
        assert number == int(str(code.value).rsplit("-", 1)[1])
        assert low <= number <= high, f"{code.value} 超出 {domain} 区间 [{low}, {high}]"


def test_helper_functions_agree_with_registry() -> None:
    """三个查询函数与注册表保持一致。"""
    for code in ErrorCode:
        spec = spec_of(code)
        assert code_to_http(code) == spec.http_status
        assert code_to_message(code) == spec.message_zh
        assert is_retryable(code) == spec.retryable


def test_spec_of_accepts_plain_string() -> None:
    """``spec_of`` 允许直接传字符串码（API 层从 JSON 拿到的就是字符串）。"""
    assert spec_of("LF-CORE-0001") is spec_of(ErrorCode.CORE_INVALID_CONFIG)


def test_retryable_codes_are_transient_only() -> None:
    """4xx 客户端错误（除 408/429）不应被标记为可重试。"""
    for code in ErrorCode:
        spec = spec_of(code)
        if spec.retryable and 400 <= spec.http_status < 500:
            assert spec.http_status in {408, 409, 423, 429}, (
                f"{code.value} 标记为可重试但 HTTP {spec.http_status} 不是瞬时错误"
            )


# ---------------------------------------------------------------------------
# 异常基类
# ---------------------------------------------------------------------------


def test_base_error_fills_code_http_and_retryable() -> None:
    """构造异常时自动补全 code / http_status / retryable。"""
    error = ConfigError("配置炸了")
    assert error.code == str(ErrorCode.CORE_INVALID_CONFIG.value)
    assert error.http_status == code_to_http(ErrorCode.CORE_INVALID_CONFIG)
    assert error.retryable is is_retryable(ErrorCode.CORE_INVALID_CONFIG)
    assert error.message == "配置炸了"
    assert error.details == {}


def test_empty_message_falls_back_to_registry_text() -> None:
    """不传 message 时用注册表里的默认文案。"""
    error = ConfigError()
    assert error.message == code_to_message(ErrorCode.CORE_INVALID_CONFIG)
    assert error.message


def test_str_and_repr_include_code() -> None:
    """字符串表示必须带错误码，便于日志 grep。"""
    error = NotFoundError("找不到", details={"id": "x"})
    assert error.code in str(error)
    assert "details=" in str(error)
    assert "NotFoundError(" in repr(error)
    assert error.code in repr(error)


def test_to_dict_is_json_serializable() -> None:
    """``to_dict()`` 直接进 API 响应与 events.payload。"""
    error = BudgetExhaustedError("超预算", details={"run_id": "R1", "spent": 6.5})
    payload = error.to_dict()
    assert set(payload) == {"code", "message", "http_status", "retryable", "details"}
    restored = json.loads(json.dumps(payload))
    assert restored["details"]["run_id"] == "R1"


def test_with_details_is_chainable_and_mutates_in_place() -> None:
    """``with_details`` 返回自身，支持 ``raise E().with_details(...)``。"""
    error = TaskNotFoundError("没这任务")
    same = error.with_details(task_id="T1").with_details(run_id="R1")
    assert same is error
    assert error.details == {"task_id": "T1", "run_id": "R1"}


def test_details_are_copied_not_aliased() -> None:
    """入参字典不应被异常对象持有引用。"""
    source = {"k": 1}
    error = InternalError("x", details=source)
    error.details["k"] = 2
    assert source["k"] == 1


# ---------------------------------------------------------------------------
# 继承体系
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("domain", "base"), sorted(DOMAIN_BASE_CLASSES.items()))
def test_every_domain_has_a_base_class(domain: str, base: type[LoopForgeError]) -> None:
    """18 个域基类都直接继承根类，且默认码属于本域。"""
    assert issubclass(base, LoopForgeError)
    assert LoopForgeError in base.__bases__
    assert str(base.default_code.value).split("-")[1] == domain


@pytest.mark.parametrize(
    ("child", "parent"),
    [
        (NotFoundError, DatabaseError),
        (UniqueViolationError, DatabaseError),
        (OptimisticLockError, DatabaseError),
        (RunNotFoundError, RunError),
        (TaskNotFoundError, TaskError),
        (LeaseAcquireFailedError, LeaseError),
        (LeaseLostError, LeaseError),
        (FencedOutError, LeaseError),
        (IdempotencyConflictError, StepError),
        (UncertainStepError, StepError),
        (ProviderAuthError, ProviderError),
        (ProviderRateLimitError, ProviderError),
        (ProviderTimeoutError, ProviderError),
        (ProviderServerError, ProviderError),
        (AllProvidersUnavailableError, ProviderError),
        (ToolNotRegisteredError, ToolError),
        (PolicyDeniedError, PolicyError),
        (ApprovalRequiredError, PolicyError),
        (BudgetExhaustedError, BudgetError),
        (ContextOverflowError, ContextError),
        (ValidationError, ApiError),
    ],
)
def test_concrete_errors_inherit_domain_base(
    child: type[LoopForgeError], parent: type[LoopForgeError]
) -> None:
    """具体异常必须挂在正确的域基类下，否则 ``except`` 会漏抓。"""
    assert issubclass(child, parent)
    assert issubclass(child, LoopForgeError)


def test_catching_domain_base_catches_children() -> None:
    """``except LeaseError`` 能抓住 FencedOutError。"""
    with pytest.raises(LeaseError):
        raise FencedOutError("被围栏挡了")
    with pytest.raises(LoopForgeError):
        raise ValidationError("参数不对")


def test_transition_and_lock_errors_carry_context() -> None:
    """状态机与乐观锁异常必须带上足以复盘的上下文。"""
    illegal = IllegalTransitionError(
        "非法迁移", details={"from": "DONE", "to": "READY"}
    )
    assert illegal.details["from"] == "DONE"
    lock = OptimisticLockError("版本冲突", details={"expected_version": 3})
    assert lock.details["expected_version"] == 3


def test_retryable_flags_on_typical_errors() -> None:
    """典型可重试 / 不可重试异常的标记要符合直觉。"""
    assert ProviderRateLimitError().retryable is True
    assert ProviderTimeoutError().retryable is True
    assert TimeoutError_().retryable is True
    assert CircuitOpenError().retryable is True
    assert ProviderAuthError().retryable is False
    assert PolicyDeniedError().retryable is False
    assert SerializationError().retryable is False


# ---------------------------------------------------------------------------
# wrap_exception
# ---------------------------------------------------------------------------


def test_wrap_exception_passes_through_loopforge_errors() -> None:
    """已经是 LoopForgeError 的原样返回。"""
    original = BudgetExhaustedError("超了")
    assert wrap_exception(original) is original


def test_wrap_exception_merges_details_on_passthrough() -> None:
    """透传时仍要合并新的上下文。"""
    original = BudgetExhaustedError("超了", details={"a": 1})
    wrapped = wrap_exception(original, details={"b": 2})
    assert wrapped is original
    assert wrapped.details == {"a": 1, "b": 2}


def test_wrap_exception_wraps_foreign_exception() -> None:
    """任意异常包成 InternalError 并保留原始类型与文本。"""
    wrapped = wrap_exception(ValueError("原始信息"))
    assert isinstance(wrapped, InternalError)
    assert wrapped.details["cause_type"] == "ValueError"
    assert wrapped.details["cause"] == "原始信息"
    assert wrapped.message == "原始信息"
    assert isinstance(wrapped.__cause__, ValueError)


def test_wrap_exception_uses_type_name_when_message_empty() -> None:
    """空文本异常退化用类型名，避免出现空消息。"""
    wrapped = wrap_exception(KeyError())
    assert wrapped.message


def test_wrap_exception_message_override() -> None:
    """显式 message 覆盖原始文本。"""
    wrapped = wrap_exception(RuntimeError("底层"), message="上层解释")
    assert wrapped.message == "上层解释"
    assert wrapped.details["cause"] == "底层"
