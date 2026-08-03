"""成本归因 —— 五维（run/task/pr/provider/model）聚合。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``budget/attribution.py``。

金额一律 :class:`decimal.Decimal`；聚合按 :class:`CostRecordRepository`
提供的原始统计实现，避免全表拉回 Python。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from loopforge.db.uow import UnitOfWork
from loopforge.models.budget import CostRecord
from loopforge.schemas.budget import (
    CostAggregate,
    CostQuery,
    CostSummary,
)

__all__ = ["CostAttribution"]


class CostAttribution:
    """成本归因器。

    :param uow: 已进入上下文的 :class:`UnitOfWork`。
    """

    def __init__(self, uow: UnitOfWork) -> None:
        """初始化归因器。"""
        self._uow: UnitOfWork = uow

    async def record(
        self,
        *,
        run_id: str,
        task_id: str | None = None,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: Decimal,
        pr_ref: str | None = None,
        llm_call_id: str | None = None,
        budget_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> CostRecord:
        """写入一条成本流水。

        :param run_id: 所属 Run。
        :param task_id: 所属 Task（可空）。
        :param provider: Provider 名。
        :param model: 模型名。
        :param prompt_tokens: 输入 token。
        :param completion_tokens: 输出 token。
        :param cost_usd: 金额（Decimal）。
        :param pr_ref: 关联 PR。
        :param llm_call_id: 关联 LLM 调用。
        :param budget_id: 计入的预算。
        :param occurred_at: 发生时刻。
        :returns: 已 flush 的 :class:`CostRecord`。
        """
        from loopforge.core.clock import get_clock

        record = await self._uow.costs.create(
            run_id=run_id,
            task_id=task_id,
            pr_ref=pr_ref,
            provider=provider,
            model=model,
            prompt_tokens=max(0, prompt_tokens),
            completion_tokens=max(0, completion_tokens),
            cost_usd=max(Decimal("0"), cost_usd),
            llm_call_id=llm_call_id,
            budget_id=budget_id,
            occurred_at=occurred_at if occurred_at is not None else get_clock().now(),
        )
        return record

    async def total_for_run(self, run_id: str) -> Decimal:
        """某 Run 总花费。"""
        return await self._uow.costs.total_for_run(run_id)

    async def breakdown_by_model(self, run_id: str) -> dict[str, Decimal]:
        """按模型维度聚合。"""
        return await self._uow.costs.breakdown_by_model(run_id)

    async def breakdown_by_task(self, run_id: str) -> dict[str, Decimal]:
        """按 Task 维度聚合。"""
        return await self._uow.costs.breakdown_by_task(run_id)

    async def summary(self, query: CostQuery) -> CostSummary:
        """按查询条件汇总（含 by_provider / by_model 聚合）。"""
        start = query.occurred_after
        end = query.occurred_before
        records = await self._uow.costs.list_in_window(
            start=start or datetime(1970, 1, 1),
            end=end or datetime(2100, 1, 1),
            run_id=query.run_id,
            limit=100_000,
        )
        by_provider: dict[str, CostAggregate] = {}
        by_model: dict[str, CostAggregate] = {}
        total_usd = Decimal("0")
        total_calls = 0
        total_tokens = 0
        for record in records:
            total_usd += record.cost_usd
            total_calls += 1
            total_tokens += record.prompt_tokens + record.completion_tokens
            provider_agg = by_provider.setdefault(
                record.provider,
                CostAggregate(key=record.provider),
            )
            provider_agg.cost_usd += record.cost_usd
            provider_agg.prompt_tokens += record.prompt_tokens
            provider_agg.completion_tokens += record.completion_tokens
            provider_agg.call_count += 1
            model_agg = by_model.setdefault(record.model, CostAggregate(key=record.model))
            model_agg.cost_usd += record.cost_usd
            model_agg.prompt_tokens += record.prompt_tokens
            model_agg.completion_tokens += record.completion_tokens
            model_agg.call_count += 1
        return CostSummary(
            total_usd=total_usd,
            total_calls=total_calls,
            total_tokens=total_tokens,
            by_provider=sorted(by_provider.values(), key=lambda agg: agg.cost_usd, reverse=True),
            by_model=sorted(by_model.values(), key=lambda agg: agg.cost_usd, reverse=True),
            window_start=query.occurred_after,
            window_end=query.occurred_before,
        )
