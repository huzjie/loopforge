"""上下文管理器 —— ``ContextManager``（冻结契约 §11.7）。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.1 / §11.7、``docs/ARCHITECTURE.md`` §8.2。

``build_context`` 是给 WP4/WP6/WP7 的**核心出口**，流程（§8.2.5）：

1. 按层配额分配 token 预算（:class:`ContextBudgetAllocator`）；
2. 强制注入全部 L0 Pinned Constraints（超配额时按优先级截断并告警）；
3. 召回 L1 工作记忆 + L2/L3 BM25 + L4 里程碑摘要；
4. 贪心装填到各层配额，超出部分进 ``dropped_items``（绝不静默丢弃）；
5. 按需触发压缩（L1 → L2 → L3 分级降级）。

另外三个冻结方法：``append_observation``（写观察）、``maybe_compress``
（压缩 + 谱系 + 保真度校验）、``pin``（钉固约束）、``check_fidelity``
（约束保真度校验）。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.clock import get_clock
from loopforge.core.config import MemorySettings, get_settings
from loopforge.core.enums import MemoryLayer, RoleName, Severity
from loopforge.core.errors import ContextOverflowError, NotFoundError
from loopforge.core.event_types import EventType
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork
from loopforge.models.memory import MemoryItem, MilestoneSummary, PinnedConstraint
from loopforge.memory.budget_allocator import ContextBudgetAllocator, LayerBudget
from loopforge.memory.compressor import CompactionResult, Compressor, FidelityReport
from loopforge.memory.context_pack import ContextBlock, ContextPack, DroppedItem
from loopforge.memory.pinned import PinnedConstraintStore
from loopforge.memory.retrieval.base import BaseRetriever, IndexDoc, RetrievedDoc
from loopforge.memory.salience import SalienceScorer

__all__ = ["ContextManager"]


class ContextManager:
    """上下文管理器 —— WP5 冻结出口。

    :param session_factory: session 工厂；``None`` 用全局。
    :param settings: 记忆配置；``None`` 用全局配置。
    :param retriever: 检索器；``None`` 时按配置创建 BM25。
    :param compressor: 压缩器；``None`` 时自动创建。
    """

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        settings: MemorySettings | None = None,
        retriever: BaseRetriever | None = None,
        compressor: Compressor | None = None,
    ) -> None:
        """初始化上下文管理器。"""
        self._session_factory: Any = session_factory if session_factory is not None else get_session_factory()
        self._settings: MemorySettings = settings if settings is not None else get_settings().memory
        self._retriever: BaseRetriever | None = retriever
        self._compressor: Compressor = compressor if compressor is not None else Compressor()
        self._scorer: SalienceScorer = SalienceScorer()

    # -- 内部工具 -----------------------------------------------------------

    def _open_uow(self) -> UnitOfWork:
        """打开一个绑定本管理器 session 工厂的事务。"""
        return UnitOfWork(session_factory=self._session_factory)

    def _ensure_retriever(self, uow: UnitOfWork) -> BaseRetriever:
        """惰性创建检索器（默认 BM25，持久化到 inverted_index 表）。"""
        if self._retriever is None:
            from loopforge.memory.retrieval.bm25 import Bm25Retriever

            bm25 = Bm25Retriever(
                session_factory=self._session_factory,
                k1=self._settings.retrieval.bm25.k1,
                b=self._settings.retrieval.bm25.b,
            )
            self._retriever = bm25
        return self._retriever

    # -- 冻结出口：build_context -------------------------------------------

    async def build_context(
        self,
        *,
        run_id: str,
        task_id: str,
        role: RoleName,
        token_budget: int,
    ) -> ContextPack:
        """按 token 预算装配上下文。

        :param run_id: 所属 Run。
        :param task_id: 当前 Task。
        :param role: 角色（决定 system 提示）。
        :param token_budget: 总输入 token 预算。
        :returns: :class:`ContextPack`（L0 必含，其余按配额贪心装填）。
        """
        if token_budget <= 0:
            raise ContextOverflowError(
                "token_budget 必须为正",
                details={"run_id": run_id, "token_budget": token_budget},
            )
        async with self._open_uow() as uow:
            allocator = ContextBudgetAllocator(
                {
                    MemoryLayer.PINNED: self._settings.layer_ratios.pinned,
                    MemoryLayer.WORKING: self._settings.layer_ratios.working,
                    MemoryLayer.EPISODIC: self._settings.layer_ratios.episodic,
                    MemoryLayer.SEMANTIC: self._settings.layer_ratios.semantic,
                    MemoryLayer.MILESTONE: self._settings.layer_ratios.milestone,
                }
            )
            budgets = allocator.allocate(token_budget)
            blocks: list[ContextBlock] = []
            dropped: list[DroppedItem] = []
            layer_used: dict[MemoryLayer, int] = {}

            # ---- L0 Pinned（强制全量，超配额按优先级截断并告警） --------
            store = PinnedConstraintStore(
                uow,
                pinned_max_ratio=self._settings.pinned_max_ratio,
                token_budget=token_budget,
            )
            constraints = await store.list_active(run_id)
            pinned_quota = budgets[MemoryLayer.PINNED].quota_tokens
            pinned_tokens = 0
            pinned_kept: list[PinnedConstraint] = []
            for constraint in constraints:
                tokens = self._estimate(constraint.text_content)
                if pinned_tokens + tokens > pinned_quota:
                    dropped.append(
                        DroppedItem(
                            ref_id=constraint.id,
                            layer=MemoryLayer.PINNED,
                            reason="pinned_quota",
                            tokens=tokens,
                        )
                    )
                    continue
                pinned_kept.append(constraint)
                pinned_tokens += tokens
                await store.record_hit(constraint.id)
            if dropped:
                uow.collect_event(
                    EventType.PINNED_QUOTA_EXCEEDED,
                    {
                        "run_id": run_id,
                        "current_tokens": pinned_tokens,
                        "quota_tokens": pinned_quota,
                        "evicted_ids": [item.ref_id for item in dropped],
                    },
                    run_id=run_id,
                    severity=Severity.WARNING,
                )
            layer_used[MemoryLayer.PINNED] = pinned_tokens
            if pinned_kept:
                pinned_text = "\n".join(
                    f"- [优先级 {item.priority}] {item.text_content}"
                    for item in sorted(pinned_kept, key=lambda c: c.priority, reverse=True)
                )
                blocks.append(
                    ContextBlock(
                        source="pinned",
                        layer=MemoryLayer.PINNED,
                        content=pinned_text,
                        tokens=pinned_tokens,
                        ref_id=",".join(item.id for item in pinned_kept),
                    )
                )

            # ---- 查询串（task 标题 + 最近错误） ---------------------------
            query = await self._build_query(uow, task_id)

            # ---- L1 工作记忆 ----------------------------------------------
            working = await uow.memory.list_by_layer(
                run_id, MemoryLayer.WORKING, include_compressed=False, limit=60
            )
            working.sort(key=lambda item: item.created_at, reverse=True)
            working_budget = budgets[MemoryLayer.WORKING]
            working_tokens = 0
            working_blocks: list[ContextBlock] = []
            for item in working:
                tokens = max(1, item.tokens or self._estimate(item.content))
                if working_tokens + tokens > working_budget.quota_tokens:
                    dropped.append(
                        DroppedItem(ref_id=item.id, layer=MemoryLayer.WORKING, reason="budget", tokens=tokens)
                    )
                    continue
                working_tokens += tokens
                working_blocks.append(
                    ContextBlock(source="working", layer=MemoryLayer.WORKING, content=item.content, tokens=tokens, ref_id=item.id)
                )
            layer_used[MemoryLayer.WORKING] = working_tokens
            blocks.extend(working_blocks)

            # ---- L2/L3 BM25 召回 ------------------------------------------
            retriever = self._ensure_retriever(uow)
            search_layers = [
                MemoryLayer.EPISODIC,
                MemoryLayer.SEMANTIC,
                MemoryLayer.MILESTONE,
            ]
            recalled: list[RetrievedDoc] = []
            if query:
                recalled = await retriever.search(
                    query,
                    top_k=self._settings.retrieval.bm25.top_k,
                    run_id=run_id,
                    layers=search_layers,
                )
            recalled_tokens = 0
            recalled_budget = (
                budgets[MemoryLayer.EPISODIC].quota_tokens
                + budgets[MemoryLayer.SEMANTIC].quota_tokens
                + budgets[MemoryLayer.MILESTONE].quota_tokens
            )
            for doc in recalled:
                tokens = self._estimate(doc.text)
                if recalled_tokens + tokens > recalled_budget:
                    dropped.append(
                        DroppedItem(ref_id=doc.doc_id, layer=doc.layer, reason="budget", tokens=tokens)
                    )
                    continue
                recalled_tokens += tokens
                source = doc.layer.value.lower()
                blocks.append(
                    ContextBlock(source=source, layer=doc.layer, content=doc.text, tokens=tokens, ref_id=doc.doc_id)
                )
            for layer in (MemoryLayer.EPISODIC, MemoryLayer.SEMANTIC):
                used = sum(block.tokens for block in blocks if block.layer is layer)
                layer_used[layer] = used

            # ---- L4 里程碑摘要 --------------------------------------------
            milestones = await uow.milestones.list_by_level(run_id, level=0, limit=5)
            if len(milestones) < 3:
                for level in (1, 2, 3):
                    milestones.extend(await uow.milestones.list_by_level(run_id, level=level, limit=3))
            milestone_budget = budgets[MemoryLayer.MILESTONE]
            milestone_tokens = 0
            for summary in milestones[:5]:
                tokens = max(1, summary.tokens or self._estimate(summary.content))
                if milestone_tokens + tokens > milestone_budget.quota_tokens:
                    break
                milestone_tokens += tokens
                blocks.append(
                    ContextBlock(source="milestone", layer=MemoryLayer.MILESTONE, content=summary.content, tokens=tokens, ref_id=summary.id)
                )
            layer_used[MemoryLayer.MILESTONE] = milestone_tokens

            # ---- 超预算时做内存内分级裁剪（L1 → L2） ----------------------
            blocks, dropped = self._trim_if_over_budget(blocks, dropped, token_budget)

            # ---- 组装消息 --------------------------------------------------
            system_prompt = self._system_prompt(role, pinned_kept)
            pack = ContextPack.from_blocks(
                blocks,
                budget_tokens=token_budget,
                dropped=dropped,
                system_prompt=system_prompt,
            )
            pack.total_tokens = sum(block.tokens for block in blocks)
            pack.layer_tokens = {layer: layer_used.get(layer, 0) for layer in MemoryLayer}

            uow.collect_event(
                EventType.MEMORY_RECALLED,
                {
                    "run_id": run_id,
                    "retriever": "bm25" if self._retriever is None else self._retriever.name,
                    "query_digest": query[:80] if query else "",
                    "hits": len(recalled),
                    "top_score": recalled[0].score if recalled else 0.0,
                },
                run_id=run_id,
                severity=Severity.DEBUG,
            )
            return pack

    # -- 冻结出口：append_observation --------------------------------------

    async def append_observation(
        self,
        *,
        step_id: str,
        content: str,
        layer: MemoryLayer,
    ) -> MemoryItem:
        """写入一条观察记忆（按 Step 解析 run/task 归属）。

        :param step_id: 来源 Step。
        :param content: 观察正文。
        :param layer: 目标记忆层（WORKING / EPISODIC / SEMANTIC）。
        :returns: 已落库的 :class:`MemoryItem`。
        """
        async with self._open_uow() as uow:
            step = await uow.steps.get(step_id)
            if step is None:
                raise NotFoundError(
                    f"Step {step_id} 不存在，无法写入观察",
                    details={"step_id": step_id},
                )
            run_id = step.run_id
            task_id = step.task_id
            from loopforge.memory.layers.base import layer_for

            mem_layer = layer_for(uow, layer)
            item = await mem_layer.append(
                run_id=run_id,
                content=content,
                task_id=task_id,
                meta={"step_id": step_id},
                salience=0.5,
            )
            return item

    # -- 冻结出口：maybe_compress -------------------------------------------

    async def maybe_compress(
        self,
        *,
        run_id: str,
        reason: str,
    ) -> CompactionResult | None:
        """按需压缩：L1 → L2 → L3 分级降级，写摘要 + 谱系 + 事件。

        :param run_id: 所属 Run。
        :param reason: 触发原因（token 阈值 / 轮次阈值 / 里程碑）。
        :returns: 发生压缩时返回 :class:`CompactionResult`；无需压缩返回 ``None``。
        """
        async with self._open_uow() as uow:
            items = await uow.memory.list_by_layer(
                run_id, MemoryLayer.EPISODIC, include_compressed=False
            )
            working = await uow.memory.list_by_layer(
                run_id, MemoryLayer.WORKING, include_compressed=False
            )
            candidates = items + working
            candidates = [
                item
                for item in candidates
                if not str(getattr(item, "compressed_into", "") or "").startswith("evicted:")
                and not str(getattr(item, "compressed_into", "") or "").startswith("rebuilt:")
            ]
            if not candidates:
                return None
            tokens_before = sum(max(1, item.tokens or self._estimate(item.content)) for item in candidates)
            target = self._settings.budget_tokens
            if tokens_before <= target:
                return None

            # L1 → L2 → L3 分级降级（M4）。
            level = 1
            text, tokens_after, loss = self._compressor.compress(candidates, level=1, target_tokens=target)
            if tokens_after > target:
                level = 2
                text, tokens_after, loss = self._compressor.compress(candidates, level=2, target_tokens=target)
            if tokens_after > target:
                level = 3
                text, tokens_after, loss = self._compressor.compress(candidates, level=3, target_tokens=target)

            # 写里程碑摘要。
            from loopforge.memory.layers.milestone import MilestoneLayer

            summary = await MilestoneLayer(uow).append(
                run_id=run_id,
                content=text,
                meta={
                    "level": level,
                    "covers_from_seq": 0,
                    "covers_to_seq": len(candidates),
                    "source_item_ids": [item.id for item in candidates],
                },
            )
            await uow.memory.mark_compressed([item.id for item in candidates], summary.id)
            for item in candidates:
                await uow.memory.add_lineage(
                    summary_id=summary.id,
                    source_item_id=item.id,
                    info_loss_score=loss,
                )

            uow.collect_event(
                EventType.MEMORY_COMPRESSED,
                {
                    "run_id": run_id,
                    "summary_id": summary.id,
                    "level": level,
                    "source_count": len(candidates),
                    "tokens_before": tokens_before,
                    "tokens_after": tokens_after,
                    "info_loss_score": loss,
                },
                run_id=run_id,
                severity=Severity.INFO,
            )
            uow.collect_event(
                EventType.MILESTONE_REACHED,
                {
                    "run_id": run_id,
                    "milestone_index": 0,
                    "summary_id": summary.id,
                    "tokens_compressed": tokens_before - tokens_after,
                },
                run_id=run_id,
                severity=Severity.INFO,
            )

            # 顺带跑一次保真度校验（M3：不满足即告警并暂停）。
            await self._check_fidelity_in_uow(uow, run_id, text, emit_violations=True)
            return CompactionResult.of(
                run_id=run_id,
                level=level,
                summary_id=summary.id,
                source_count=len(candidates),
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                info_loss_score=loss,
            )

    # -- 冻结出口：pin ------------------------------------------------------

    async def pin(
        self,
        *,
        run_id: str,
        text: str,
        origin: str,
        priority: int = 100,
    ) -> PinnedConstraint:
        """钉固一条约束（自动去重 + 硬配额守卫）。

        :param run_id: 所属 Run。
        :param text: 约束原文。
        :param origin: 来源（user/planner/policy/system）。
        :param priority: 优先级（默认 100）。
        :returns: 已落库的 :class:`PinnedConstraint`。
        """
        async with self._open_uow() as uow:
            store = PinnedConstraintStore(
                uow,
                pinned_max_ratio=self._settings.pinned_max_ratio,
                token_budget=self._settings.budget_tokens,
            )
            constraint = await store.add(
                run_id=run_id, text=text, origin=origin, priority=priority
            )
            return constraint

    # -- 冻结出口：check_fidelity -------------------------------------------

    async def check_fidelity(self, *, run_id: str, plan_text: str) -> FidelityReport:
        """校验全部生效约束的关键事实是否仍被计划文本覆盖。

        :param run_id: 所属 Run。
        :param plan_text: 待校验的上下文 / 计划文本。
        :returns: :class:`FidelityReport`。
        """
        async with self._open_uow() as uow:
            return await self._check_fidelity_in_uow(uow, run_id, plan_text, emit_violations=True)

    # -- 内部：保真度 -------------------------------------------------------

    async def _check_fidelity_in_uow(
        self,
        uow: UnitOfWork,
        run_id: str,
        plan_text: str,
        *,
        emit_violations: bool,
    ) -> FidelityReport:
        """在既有事务内执行保真度校验。"""
        constraints = await uow.pinned.list_active(run_id)
        report = self._compressor.check_fidelity(
            run_id=run_id, constraints=constraints, plan_text=plan_text
        )
        if emit_violations and report.violations:
            for violation in report.violations:
                uow.collect_event(
                    EventType.CONSTRAINT_VIOLATION,
                    {
                        "run_id": run_id,
                        "constraint_id": violation.constraint_id,
                        "constraint_text": violation.constraint_text,
                        "evidence": violation.evidence,
                        "severity": violation.severity.value,
                    },
                    run_id=run_id,
                    severity=Severity.ERROR,
                )
                # 把被违背的约束重新注入 L1 顶部。
                await uow.memory.add_item(
                    run_id=run_id,
                    content=f"[约束重申] {violation.constraint_text}",
                    layer=MemoryLayer.WORKING,
                    tokens=self._estimate(violation.constraint_text),
                    salience=0.95,
                    meta={"kind": "constraint_reinforce"},
                )
                if violation.constraint_id:
                    await uow.pinned.record_hit(violation.constraint_id)
        return report

    # -- 内部工具 -----------------------------------------------------------

    @staticmethod
    def _estimate(text: str) -> int:
        """token 估算（延迟 import 避免循环）。"""
        from loopforge.memory.layers.base import estimate_tokens

        return estimate_tokens(text)

    async def _build_query(self, uow: UnitOfWork, task_id: str) -> str:
        """构造检索查询串：Task 标题 + 最近失败关键词。"""
        parts: list[str] = []
        if task_id:
            task = await uow.tasks.get(task_id)
            if task is not None:
                title = getattr(task, "title", "") or ""
                if title:
                    parts.append(title)
                task_key = getattr(task, "task_key", "") or ""
                if task_key:
                    parts.append(task_key)
        parts.append("错误 失败 error failed exception bug")
        return " ".join(parts)

    @staticmethod
    def _system_prompt(role: RoleName, constraints: list[PinnedConstraint]) -> str:
        """生成 system 提示：角色 + L0 约束。"""
        lines = [f"你是 LoopForge 的 {role.value} 角色。", "以下约束不可违反："]
        if constraints:
            # priority 越大越重要（与 WP1 仓储排序一致）。
            for constraint in sorted(constraints, key=lambda c: c.priority, reverse=True):
                lines.append(f"- {constraint.text_content}")
        else:
            lines.append("- （暂无）")
        return "\n".join(lines)

    def _trim_if_over_budget(
        self,
        blocks: list[ContextBlock],
        dropped: list[DroppedItem],
        token_budget: int,
    ) -> tuple[list[ContextBlock], list[DroppedItem]]:
        """内存内分级裁剪：总 token 超预算时先 L1 结构化，再 L2 抽取。"""
        total = sum(block.tokens for block in blocks)
        if total <= token_budget:
            return blocks, dropped
        # L1：对 working/episodic 块做结构化截断。
        trimmed: list[ContextBlock] = []
        for block in blocks:
            if block.source in ("working", "episodic", "semantic") and block.tokens > 256:
                from loopforge.memory.compressor import Compressor

                new_content = Compressor._trim_structural(block.content)
                new_tokens = self._estimate(new_content)
                if new_tokens < block.tokens:
                    dropped.append(
                        DroppedItem(ref_id=block.ref_id or block.source, layer=block.layer, reason="l1_trim", tokens=block.tokens - new_tokens)
                    )
                    trimmed.append(
                        ContextBlock(source=block.source, layer=block.layer, content=new_content, tokens=new_tokens, ref_id=block.ref_id)
                    )
                    continue
            trimmed.append(block)
        blocks = trimmed
        total = sum(block.tokens for block in blocks)
        if total <= token_budget:
            return blocks, dropped
        # L2：按显著性裁掉尾部非关键块。
        while blocks and total > token_budget:
            # 保留 pinned；从末尾裁。
            victim_index = None
            for index in range(len(blocks) - 1, 0, -1):
                if blocks[index].source != "pinned":
                    victim_index = index
                    break
            if victim_index is None:
                break
            victim = blocks.pop(victim_index)
            total -= victim.tokens
            dropped.append(
                DroppedItem(ref_id=victim.ref_id or victim.source, layer=victim.layer, reason="l2_drop", tokens=victim.tokens)
            )
        return blocks, dropped
