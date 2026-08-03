"""Pinned Constraints —— L0 不可压缩约束集。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.1、``docs/ARCHITECTURE.md`` §8.2.2。

铁律（M1）：**L0 永不压缩、永不裁剪**，硬配额 ``pinned_max_ratio``
（默认 8%）。配额不足时**宁可裁掉 L2/L3 也不动 L0**；若 L0 自身超配额，
按优先级截断最低优先级并发出 ``PINNED_QUOTA_EXCEEDED`` 事件，**绝不静默丢弃**。

冲突检测：同一 Run 内两条约束若语义互斥（一方含否定词、另一方为肯定，
且共享关键主题词），判定为冲突并在 :meth:`detect_conflicts` 中返回。
"""

from __future__ import annotations

from datetime import datetime

from loopforge.core.clock import get_clock
from loopforge.core.enums import MemoryLayer, Severity
from loopforge.core.event_types import EventType
from loopforge.core.hashing import sha256_hex
from loopforge.db.uow import UnitOfWork
from loopforge.models.memory import PinnedConstraint

__all__ = [
    "PinnedConstraintStore",
    "PinnedConflict",
    "negation_terms",
    "detect_conflict_pair",
]

#: 否定词集合 —— 约束中出现这些词表示「禁止」。
NEGATION_TERMS: frozenset[str] = frozenset(
    {
        "禁止", "不要", "不得", "不允许", "严禁", "不可", "不能", "勿",
        "never", "must not", "cannot", "should not", "shall not", "no ",
        "do not", "don't", "禁止使用",
    }
)

#: 肯定词集合 —— 与否定词相对的强约束。
AFFIRMATION_TERMS: frozenset[str] = frozenset(
    {
        "必须", "一定要", "务必", "应当", "需要", "always", "must", "required",
        "shall", "need to", "ensure",
    }
)


class PinnedConflict:
    """两条钉固约束之间的语义冲突描述。"""

    __slots__ = ("left_id", "right_id", "topic", "severity")

    def __init__(self, left_id: str, right_id: str, topic: str) -> None:
        """初始化冲突描述。

        :param left_id: 左侧约束 ID。
        :param right_id: 右侧约束 ID。
        :param topic: 冲突主题词。
        """
        self.left_id: str = left_id
        self.right_id: str = right_id
        self.topic: str = topic
        self.severity: Severity = Severity.WARNING

    def to_dict(self) -> dict[str, object]:
        """转字典（事件 / 日志用）。"""
        return {
            "left_id": self.left_id,
            "right_id": self.right_id,
            "topic": self.topic,
            "severity": self.severity.value,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return f"PinnedConflict(left={self.left_id}, right={self.right_id}, topic={self.topic!r})"


def _topic_terms(text: str) -> set[str]:
    """提取约束中的主题词：去掉否定/肯定词后剩余的关键词。"""
    lowered = text.lower()
    for term in NEGATION_TERMS | AFFIRMATION_TERMS:
        lowered = lowered.replace(term, " ")
    # 取长度 >= 2 的字母数字串作为主题词。
    words = {word for word in lowered.replace("/", " ").replace("_", " ").split() if len(word) >= 2}
    return words


def detect_conflict_pair(left: PinnedConstraint, right: PinnedConstraint) -> PinnedConflict | None:
    """检测两条约束是否冲突。

    规则：主题词有交集，且一条含否定词、另一条含肯定词 → 冲突。

    :param left: 约束 A。
    :param right: 约束 B。
    :returns: 冲突描述；不冲突返回 ``None``。
    """
    left_neg = any(term in left.text_content.lower() for term in NEGATION_TERMS)
    right_neg = any(term in right.text_content.lower() for term in NEGATION_TERMS)
    if left_neg == right_neg:
        return None
    left_topics = _topic_terms(left.text_content)
    right_topics = _topic_terms(right.text_content)
    shared = left_topics & right_topics
    if not shared:
        return None
    topic = sorted(shared, key=len, reverse=True)[0]
    return PinnedConflict(left.id, right.id, topic)


class PinnedConstraintStore:
    """L0 钉固约束存储 —— 封装 :class:`PinnedConstraintRepository`。

    :param uow: 已进入上下文的 :class:`UnitOfWork`；调用方负责 commit。
    :param pinned_max_ratio: L0 硬配额比例（默认 0.08）。
    :param token_budget: 当前上下文 token 预算（用于计算硬配额）。
    :param estimate_tokens: 文本 token 估算函数；缺省用 :func:`estimate_tokens`。
    """

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        pinned_max_ratio: float = 0.08,
        token_budget: int = 128_000,
        estimate_tokens: "callable[[str], int] | None" = None,
    ) -> None:
        from loopforge.memory.layers.base import estimate_tokens as _default_estimate

        self._uow: UnitOfWork = uow
        self.pinned_max_ratio: float = pinned_max_ratio
        self.token_budget: int = max(0, token_budget)
        self._estimate = estimate_tokens or _default_estimate

    @property
    def quota_tokens(self) -> int:
        """L0 硬配额 token 数。"""
        return int(self.token_budget * self.pinned_max_ratio)

    # -- 增删查 -------------------------------------------------------------

    async def add(
        self,
        *,
        run_id: str,
        text: str,
        origin: str = "user",
        priority: int = 100,
        origin_step_id: str | None = None,
    ) -> PinnedConstraint:
        """钉固一条约束（自动去重 + 硬配额守卫）。

        :param run_id: 所属 Run。
        :param text: 约束原文。
        :param origin: 来源（user/planner/policy/system）。
        :param priority: 优先级（越小越靠前）。
        :param origin_step_id: 来源 Step。
        :returns: 已落库的约束（同文本去重时返回已有记录）。
        """
        constraint = await self._uow.pinned.pin(
            run_id=run_id,
            text=text,
            priority=priority,
            origin=origin,
            origin_step_id=origin_step_id,
        )
        self._uow.collect_event(
            EventType.PINNED_CONSTRAINT_ADDED,
            {
                "run_id": run_id,
                "constraint_id": constraint.id,
                "origin": constraint.origin,
                "priority": constraint.priority,
                "text_digest": constraint.text_hash,
            },
            run_id=run_id,
            severity=Severity.INFO,
        )
        await self._enforce_quota(run_id=run_id, keep_id=constraint.id)
        return constraint

    async def list_active(self, run_id: str) -> list[PinnedConstraint]:
        """列出生效中的约束（按优先级倒序）。"""
        return await self._uow.pinned.list_active(run_id)

    async def deactivate(self, constraint_id: str) -> bool:
        """软下线一条约束（不删除）。"""
        return await self._uow.pinned.deactivate(constraint_id)

    async def record_hit(self, constraint_id: str) -> bool:
        """命中计数 +1（统计真正被注入过上下文的约束）。"""
        return await self._uow.pinned.record_hit(constraint_id)

    async def count_tokens(self, run_id: str) -> int:
        """当前生效约束的总 token 数。"""
        constraints = await self.list_active(run_id)
        return sum(self._estimate(item.text_content) for item in constraints)

    async def detect_conflicts(self, run_id: str) -> list[PinnedConflict]:
        """检测该 Run 全部生效约束之间的冲突。"""
        constraints = await self.list_active(run_id)
        conflicts: list[PinnedConflict] = []
        for index, left in enumerate(constraints):
            for right in constraints[index + 1 :]:
                conflict = detect_conflict_pair(left, right)
                if conflict is not None:
                    conflicts.append(conflict)
        return conflicts

    # -- 配额守卫 -----------------------------------------------------------

    async def _enforce_quota(self, *, run_id: str, keep_id: str) -> None:
        """硬配额守卫：L0 超配额时按优先级截断，绝不静默丢弃。

        截断策略：优先裁掉低优先级（priority 数值大）的约束；被裁的约束
        软下线（active=False），并发出 ``PINNED_QUOTA_EXCEEDED`` 事件。
        """
        constraints = await self.list_active(run_id)
        total = sum(self._estimate(item.text_content) for item in constraints)
        if total <= self.quota_tokens:
            return
        evicted: list[str] = []
        # 优先级语义与 WP1 仓储一致：priority 越大越重要、排越前。
        # 超配额时优先裁掉 priority 最小（最不重要）的约束；最后保留 keep_id。
        ordered = sorted(
            (item for item in constraints if item.id != keep_id),
            key=lambda item: (item.priority, item.created_at),
        )
        for item in ordered:
            if total <= self.quota_tokens:
                break
            await self.deactivate(item.id)
            total -= self._estimate(item.text_content)
            evicted.append(item.id)
        if evicted:
            self._uow.collect_event(
                EventType.PINNED_QUOTA_EXCEEDED,
                {
                    "run_id": run_id,
                    "current_tokens": total,
                    "quota_tokens": self.quota_tokens,
                    "evicted_ids": evicted,
                },
                run_id=run_id,
                severity=Severity.WARNING,
            )


#: 便捷引用：text_hash 计算（与 repo 保持一致）。
def pinned_text_hash(text: str) -> str:
    """计算约束正文的 sha256（Run 内去重键）。"""
    return sha256_hex(text)
