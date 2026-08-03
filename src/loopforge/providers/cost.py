"""成本计量与落账 —— 把一次 LLM 调用变成 ``llm_calls`` + ``cost_records`` 两行。

职责边界
--------
* :mod:`loopforge.providers.pricing` —— **纯函数**，只做「token → USD」换算；
* 本模块 —— **有副作用**，负责持久化与聚合，是 WP5 预算治理的数据源头。

铁律
----
1. 写库一律 ``async with UnitOfWork() as uow:``，退出自动 commit；
2. 金额全程 :class:`~decimal.Decimal`，禁止 ``float``；
3. 时间只走 :class:`~loopforge.core.clock.Clock`；
4. ``request`` / ``response`` 由 ``LlmCallRepository.record`` 强制脱敏，
   本模块额外做一次「不落 prompt 正文」的裁剪（只留结构与摘要）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from loopforge.core.clock import Clock, get_clock
from loopforge.core.enums import RoleName
from loopforge.core.hashing import short_hash
from loopforge.core.logging import get_logger
from loopforge.db.uow import UnitOfWork
from loopforge.providers.pricing import (
    ModelNotPricedError,
    actual_cost,
    estimate_cost,
    quantize_money,
)
from loopforge.providers.types import ChatRequest, ChatResult, Message, Usage

__all__ = [
    "CostEntry",
    "CostMeter",
    "CostAggregate",
    "summarize_request",
    "summarize_result",
    "estimate_request_cost",
]

_LOG = get_logger(__name__)

#: 落库时单条摘要文本的长度上限。
_SUMMARY_LIMIT = 160


@dataclass(slots=True, frozen=True)
class CostEntry:
    """一次调用的成本条目（内存结构，落库前后共用）。

    Attributes:
        provider: Provider 注册名。
        model: 模型名。
        usage: token 用量。
        cost_usd: 实际花费。
        latency_ms: 端到端耗时。
        run_id / task_id / step_id: 归因维度。
        llm_call_id: 落库后回填的 ``llm_calls.id``。
        cost_record_id: 落库后回填的 ``cost_records.id``。
    """

    provider: str
    model: str
    usage: Usage
    cost_usd: Decimal
    latency_ms: int = 0
    run_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    role: RoleName | None = None
    attempt: int = 0
    failover_from: str | None = None
    finish_reason: str | None = None
    error_code: str | None = None
    llm_call_id: str | None = None
    cost_record_id: str | None = None

    @property
    def total_tokens(self) -> int:
        """输入 + 输出 token。"""
        return self.usage.total_tokens


@dataclass(slots=True)
class CostAggregate:
    """内存聚合器 —— 无 DB 场景（CLI ``--mock`` / 单测）下的轻量账本。"""

    total_usd: Decimal = Decimal("0")
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    calls: int = 0
    by_model: dict[str, Decimal] = field(default_factory=dict)
    by_provider: dict[str, Decimal] = field(default_factory=dict)

    def add(self, entry: CostEntry) -> CostAggregate:
        """累加一条成本条目。

        Args:
            entry: 成本条目。

        Returns:
            自身，便于链式调用。
        """
        self.total_usd = quantize_money(self.total_usd + entry.cost_usd)
        self.prompt_tokens += entry.usage.prompt_tokens
        self.completion_tokens += entry.usage.completion_tokens
        self.cached_tokens += entry.usage.cached_tokens
        self.calls += 1
        model_key = f"{entry.provider}/{entry.model}"
        self.by_model[model_key] = quantize_money(
            self.by_model.get(model_key, Decimal("0")) + entry.cost_usd
        )
        self.by_provider[entry.provider] = quantize_money(
            self.by_provider.get(entry.provider, Decimal("0")) + entry.cost_usd
        )
        return self

    def snapshot(self) -> dict[str, Any]:
        """导出可 JSON 化的快照。

        Returns:
            纯 Python 字典（金额为字符串，避免 float 精度丢失）。
        """
        return {
            "total_usd": str(self.total_usd),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "calls": self.calls,
            "by_model": {key: str(value) for key, value in sorted(self.by_model.items())},
            "by_provider": {key: str(value) for key, value in sorted(self.by_provider.items())},
        }


def summarize_request(request: ChatRequest) -> dict[str, Any]:
    """把请求压成**不含 prompt 正文**的可落库摘要。

    只保留结构信息（消息条数、角色分布、内容摘要哈希、工具名），
    满足 ``TASK_BREAKDOWN`` §12.4「禁止把完整 prompt 写入日志/明文落库」。

    Args:
        request: 对话请求。

    Returns:
        摘要字典。
    """
    role_counts: dict[str, int] = {}
    for message in request.messages:
        role_counts[message.role] = role_counts.get(message.role, 0) + 1
    joined = "\n".join(message.text() for message in request.messages)
    return {
        "model": request.model,
        "message_count": len(request.messages),
        "role_counts": dict(sorted(role_counts.items())),
        "content_digest": short_hash(joined, 16) if joined else "",
        "tools": list(request.tool_names()),
        "tool_choice": request.tool_choice,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "seed": request.seed,
        "metadata": dict(sorted(request.metadata.items())),
    }


def summarize_result(result: ChatResult) -> dict[str, Any]:
    """把响应压成可落库摘要（截断正文，保留工具调用结构）。

    Args:
        result: 对话结果。

    Returns:
        摘要字典。
    """
    excerpt = " ".join(result.content.split())
    if len(excerpt) > _SUMMARY_LIMIT:
        excerpt = f"{excerpt[:_SUMMARY_LIMIT]}…"
    return {
        "model": result.model,
        "finish_reason": result.finish_reason,
        "content_excerpt": excerpt,
        "content_length": len(result.content),
        "content_digest": short_hash(result.content, 16) if result.content else "",
        "tool_calls": [
            {"id": call.id, "name": call.name, "arg_keys": sorted(call.arguments)}
            for call in result.tool_calls
        ],
        "latency_ms": result.latency_ms,
    }


def estimate_request_cost(
    provider: str,
    request: ChatRequest,
    *,
    messages: Sequence[Message] | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> Decimal:
    """请求级预估花费（BudgetGuard 在真正发请求前调用）。

    Args:
        provider: Provider 注册名。
        request: 对话请求。
        messages: 覆盖用的消息序列（缺省用 ``request.messages``）。
        prompt_tokens: 直接给定输入 token，跳过估算。
        completion_tokens: 直接给定输出 token；缺省按 ``max_tokens`` 四分之一保守估。

    Returns:
        预估花费（USD）。
    """
    from loopforge.providers.token_counter import count_messages

    if prompt_tokens is None:
        source = list(messages) if messages is not None else list(request.messages)
        prompt_tokens = count_messages(source, request.model, tools=request.tools)
    if completion_tokens is None:
        completion_tokens = max(1, (request.max_tokens or 4096) // 4)
    try:
        return estimate_cost(
            provider,
            request.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    except ModelNotPricedError:
        _LOG.warning(
            "pricing.missing", provider=provider, model=request.model, phase="estimate"
        )
        return Decimal("0E-8")


class CostMeter:
    """成本计量器 —— 算钱 + 落账 + 内存聚合三合一。

    典型用法（有 DB）::

        meter = CostMeter()
        entry = await meter.record(
            run_id=run.id, provider="dashscope", model="qwen3.8-max",
            usage=result.usage, latency_ms=result.latency_ms,
            request=request, result=result,
        )

    无 DB 场景（CLI / 单测）传 ``persist=False``，只走内存聚合。
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        uow_factory: Callable[[], UnitOfWork] | None = None,
        persist: bool = True,
    ) -> None:
        """初始化计量器。

        Args:
            clock: 时间源；``None`` 用进程默认时钟。
            uow_factory: UnitOfWork 工厂；``None`` 用 ``UnitOfWork()``。
            persist: 是否落库。``False`` 时只做内存聚合。
        """
        self.clock: Clock = clock if clock is not None else get_clock()
        self._uow_factory: Callable[[], UnitOfWork] = uow_factory or UnitOfWork
        self.persist: bool = persist
        self.aggregate: CostAggregate = CostAggregate()

    # ------------------------------------------------------------------ 计算

    def compute(self, provider: str, model: str, usage: Usage) -> Decimal:
        """算出一次调用的实际花费；无价目时记 0 并告警。

        Args:
            provider: Provider 注册名。
            model: 模型名。
            usage: 真实用量。

        Returns:
            花费（USD，8 位小数）。
        """
        try:
            return actual_cost(provider, model, usage)
        except ModelNotPricedError:
            _LOG.warning("pricing.missing", provider=provider, model=model, phase="actual")
            return Decimal("0E-8")

    # ------------------------------------------------------------------ 落账

    async def record(
        self,
        *,
        provider: str,
        model: str,
        usage: Usage,
        latency_ms: int = 0,
        run_id: str | None = None,
        task_id: str | None = None,
        step_id: str | None = None,
        role: RoleName | None = None,
        attempt: int = 0,
        failover_from: str | None = None,
        finish_reason: str | None = None,
        error_code: str | None = None,
        prompt_version: str | None = None,
        pr_ref: str | None = None,
        request: ChatRequest | None = None,
        result: ChatResult | None = None,
    ) -> CostEntry:
        """算钱并落账。

        写两张表：

        * ``llm_calls`` —— 调用级遥测（脱敏后的请求/响应摘要 + token + 花费）；
        * ``cost_records`` —— 五维归因流水（仅当 ``run_id`` 存在时写）。

        Args:
            provider: Provider 注册名。
            model: 模型名。
            usage: 真实用量。
            latency_ms: 端到端耗时。
            run_id: 归属 Run；``None`` 时跳过 ``cost_records``。
            task_id: 归属 Task。
            step_id: 归属 Step。
            role: 触发调用的角色。
            attempt: 第几次尝试（0-based）。
            failover_from: 若为故障转移调用，记录原 Provider 名。
            finish_reason: 结束原因。
            error_code: 失败时的 LoopForge 错误码。
            prompt_version: 提示词版本。
            pr_ref: PR 归因。
            request: 原始请求（会被压成摘要）。
            result: 原始响应（会被压成摘要）。

        Returns:
            :class:`CostEntry`，已回填数据库主键（未落库时为 ``None``）。
        """
        cost_usd = self.compute(provider, model, usage)
        entry = CostEntry(
            provider=provider,
            model=model,
            usage=usage,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            run_id=run_id,
            task_id=task_id,
            step_id=step_id,
            role=role,
            attempt=attempt,
            failover_from=failover_from,
            finish_reason=finish_reason,
            error_code=error_code,
        )
        self.aggregate.add(entry)
        if not self.persist:
            return entry

        request_summary = summarize_request(request) if request is not None else {}
        response_summary = summarize_result(result) if result is not None else {}

        async with self._uow_factory() as uow:
            llm_call = await uow.llm_calls.record(
                step_id=step_id,
                provider=provider,
                model=model,
                role=role,
                request=request_summary,
                response=response_summary,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cached_tokens=usage.cached_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                attempt=attempt,
                failover_from=failover_from,
                finish_reason=finish_reason,
                prompt_version=prompt_version,
                error_code=error_code,
                created_at=self.clock.now(),
            )
            cost_record_id: str | None = None
            if run_id:
                record = await uow.costs.create(
                    run_id=run_id,
                    task_id=task_id,
                    pr_ref=pr_ref,
                    provider=provider,
                    model=model,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    cost_usd=cost_usd,
                    llm_call_id=llm_call.id,
                    occurred_at=self.clock.now(),
                )
                cost_record_id = str(record.id)
            llm_call_id = str(llm_call.id)

        return CostEntry(
            provider=entry.provider,
            model=entry.model,
            usage=entry.usage,
            cost_usd=entry.cost_usd,
            latency_ms=entry.latency_ms,
            run_id=entry.run_id,
            task_id=entry.task_id,
            step_id=entry.step_id,
            role=entry.role,
            attempt=entry.attempt,
            failover_from=entry.failover_from,
            finish_reason=entry.finish_reason,
            error_code=entry.error_code,
            llm_call_id=llm_call_id,
            cost_record_id=cost_record_id,
        )

    async def record_result(
        self,
        *,
        provider: str,
        request: ChatRequest,
        result: ChatResult,
        run_id: str | None = None,
        task_id: str | None = None,
        step_id: str | None = None,
        attempt: int = 0,
        failover_from: str | None = None,
        prompt_version: str | None = None,
        pr_ref: str | None = None,
    ) -> CostEntry:
        """:meth:`record` 的便捷包装 —— 直接吃 ``ChatRequest`` / ``ChatResult``。

        归因维度优先取显式入参，其次从 ``request.metadata`` 里读。

        Args:
            provider: Provider 注册名。
            request: 原始请求。
            result: 原始响应。
            run_id: 归属 Run（缺省读 ``metadata['run_id']``）。
            task_id: 归属 Task（缺省读 ``metadata['task_id']``）。
            step_id: 归属 Step（缺省读 ``metadata['step_id']``）。
            attempt: 第几次尝试。
            failover_from: 故障转移来源。
            prompt_version: 提示词版本。
            pr_ref: PR 归因。

        Returns:
            :class:`CostEntry`。
        """
        return await self.record(
            provider=provider,
            model=result.model or request.model,
            usage=result.usage,
            latency_ms=result.latency_ms,
            run_id=run_id or (request.meta("run_id") or None),
            task_id=task_id or (request.meta("task_id") or None),
            step_id=step_id or (request.meta("step_id") or None),
            role=request.role_name(),
            attempt=attempt or request.meta_int("attempt", 0),
            failover_from=failover_from,
            finish_reason=result.finish_reason,
            prompt_version=prompt_version,
            pr_ref=pr_ref,
            request=request,
            result=result,
        )

    def reset(self) -> None:
        """清空内存聚合（不影响已落库数据）。"""
        self.aggregate = CostAggregate()
