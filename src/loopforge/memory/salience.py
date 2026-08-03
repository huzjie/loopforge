"""显著性打分 —— 决定「哪些记忆先被压缩 / 哪些先被召回」。

来源：``docs/ARCHITECTURE.md`` §8.2.4 召回排序公式::

    排序 = 0.55 * bm25_norm + 0.25 * salience + 0.20 * recency_decay

本模块只负责 salience 与 recency 两个分量；bm25 分量由检索器提供。

* **时间衰减**：指数衰减，半衰期可配（默认 24h）。
* **命中反馈**：被检索命中 / 被校验命中的约束会获得提升（提升上限 0.2）。
"""

from __future__ import annotations

from datetime import datetime

from loopforge.core.clock import ensure_utc

__all__ = [
    "DEFAULT_HALF_LIFE_S",
    "SALIENCE_MIN",
    "SALIENCE_MAX",
    "HIT_BOOST",
    "HIT_BOOST_CAP",
    "time_decay",
    "salience_with_decay",
    "boost_by_hits",
    "SalienceScorer",
]

#: 时间衰减半衰期（秒），默认 24 小时。
DEFAULT_HALF_LIFE_S: float = 24 * 3600.0

#: salience 合法区间。
SALIENCE_MIN: float = 0.0
SALIENCE_MAX: float = 1.0

#: 每次命中反馈的提升量。
HIT_BOOST: float = 0.02
#: 命中反馈累计上限（防止单一记忆被反复命中刷到 1.0）。
HIT_BOOST_CAP: float = 0.2


def time_decay(
    created_at: datetime,
    now: datetime,
    *,
    half_life_s: float = DEFAULT_HALF_LIFE_S,
) -> float:
    """指数时间衰减因子。

    :param created_at: 条目创建时刻（tz-aware）。
    :param now: 当前时刻（tz-aware）。
    :param half_life_s: 半衰期秒数；<=0 表示不衰减。
    :returns: ``[0, 1]`` 的衰减因子，越旧越接近 0。
    """
    if half_life_s <= 0:
        return 1.0
    age_s = (ensure_utc(now) - ensure_utc(created_at)).total_seconds()
    if age_s <= 0:
        return 1.0
    return max(SALIENCE_MIN, min(SALIENCE_MAX, 2.0 ** (-age_s / half_life_s)))


def boost_by_hits(hit_count: int) -> float:
    """按命中次数给出提升量，封顶 ``HIT_BOOST_CAP``。

    :param hit_count: 命中次数。
    :returns: 提升量 ``[0, HIT_BOOST_CAP]``。
    """
    if hit_count <= 0:
        return 0.0
    return min(HIT_BOOST_CAP, HIT_BOOST * hit_count)


def salience_with_decay(
    base_salience: float,
    created_at: datetime,
    now: datetime,
    *,
    hit_count: int = 0,
    half_life_s: float = DEFAULT_HALF_LIFE_S,
) -> float:
    """综合 salience：基础分 × 时间衰减 + 命中提升。

    :param base_salience: 写入时的基础显著性（0~1）。
    :param created_at: 创建时刻。
    :param now: 当前时刻。
    :param hit_count: 命中次数。
    :param half_life_s: 半衰期秒数。
    :returns: 夹到 ``[0, 1]`` 的综合分。
    """
    decayed = base_salience * time_decay(created_at, now, half_life_s=half_life_s)
    boosted = decayed + boost_by_hits(hit_count)
    return max(SALIENCE_MIN, min(SALIENCE_MAX, boosted))


class SalienceScorer:
    """进程级显著性打分器（无状态，可共享）。

    用法::

        scorer = SalienceScorer(half_life_s=3600)
        score = scorer.score(base=0.6, created_at=item.created_at, now=clock.now())
    """

    def __init__(self, *, half_life_s: float = DEFAULT_HALF_LIFE_S) -> None:
        """初始化打分器。

        :param half_life_s: 时间衰减半衰期（秒）。
        """
        self.half_life_s: float = half_life_s

    def score(
        self,
        *,
        base: float,
        created_at: datetime,
        now: datetime,
        hit_count: int = 0,
    ) -> float:
        """计算综合显著性。

        :param base: 基础显著性 0~1。
        :param created_at: 条目创建时刻。
        :param now: 当前时刻。
        :param hit_count: 命中次数。
        :returns: 综合分 0~1。
        """
        return salience_with_decay(
            base,
            created_at,
            now,
            hit_count=hit_count,
            half_life_s=self.half_life_s,
        )

    def rank(
        self,
        items: list[tuple[float, datetime, int]],
        *,
        now: datetime,
    ) -> list[float]:
        """批量打分。

        :param items: ``[(base_salience, created_at, hit_count), ...]``。
        :param now: 当前时刻。
        :returns: 与输入等长的综合分列表。
        """
        return [
            self.score(base=base, created_at=created, now=now, hit_count=hits)
            for base, created, hits in items
        ]
