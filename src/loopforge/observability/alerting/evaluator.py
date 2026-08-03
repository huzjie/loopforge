"""告警评估器 —— 去抖 + 触发/消解状态机 + 多通道投递。

每个规则独立维护状态：``firing`` 与 ``since``（连续满足的起始时刻）。
满足 ``duration_s`` 后触发一次 ``ALERT_FIRED``，恢复后发 ``ALERT_RESOLVED``。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loopforge.core.clock import Clock, get_clock
from loopforge.core.enums import Severity
from loopforge.core.event_types import EventType
from loopforge.core.logging import get_logger
from loopforge.core.event_bus import EventBus, get_event_bus
from loopforge.core.event_types import AlertPayload
from loopforge.observability.alerting.channels.base import AlertChannel
from loopforge.observability.alerting.rules import AlertRule

__all__ = ["AlertState", "AlertEvaluator"]


@dataclass
class AlertState:
    """单个规则的评估状态。"""

    rule: AlertRule
    firing: bool = False
    since: datetime | None = None
    last_value: float = 0.0
    fired_at: datetime | None = None
    resolved_at: datetime | None = None


class AlertEvaluator:
    """规则评估器。

    :param rules: 规则列表。
    :param channels: 投递通道列表。
    :param clock: 时间源。
    :param event_bus: 事件总线；``None`` 时用全局单例。
    """

    def __init__(
        self,
        rules: list[AlertRule],
        *,
        channels: list[AlertChannel] | None = None,
        clock: Clock | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._rules = {rule.name: AlertState(rule) for rule in rules}
        self._channels: list[AlertChannel] = list(channels or [])
        self._clock = clock or get_clock()
        self._bus = event_bus
        self._logger = get_logger(__name__)

    @property
    def states(self) -> dict[str, AlertState]:
        """全部规则状态（测试与面板用）。"""
        return self._rules

    def register_channel(self, channel: AlertChannel) -> None:
        """追加投递通道。"""
        self._channels.append(channel)

    async def evaluate(self, metric_name: str, value: float, labels: dict[str, Any] | None = None) -> list[str]:
        """对一条指标采样评估所有相关规则。

        :returns: 本次新触发的规则名列表。
        """
        labels = labels or {}
        fired: list[str] = []
        for name, state in self._rules.items():
            rule = state.rule
            if rule.metric != metric_name:
                continue
            if not self._labels_match(rule, labels):
                continue
            triggered = rule.evaluate(value)
            now = self._clock.now()
            state.last_value = value
            if triggered:
                if state.since is None:
                    state.since = now
                elapsed = (now - state.since).total_seconds()
                if not state.firing and elapsed >= rule.duration_s:
                    state.firing = True
                    state.fired_at = now
                    fired.append(rule.name)
                    await self._fire(state)
            else:
                if state.firing:
                    state.firing = False
                    state.resolved_at = now
                    await self._resolve(state)
                state.since = None
        return fired

    @staticmethod
    def _labels_match(rule: AlertRule, labels: dict[str, Any]) -> bool:
        """规则声明的 label 必须全部命中采样 label。"""
        for key, expected in rule.labels.items():
            if str(labels.get(key, "")) != str(expected):
                return False
        return True

    async def _fire(self, state: AlertState) -> None:
        """触发告警：投递通道 + 发事件。"""
        payload = AlertPayload(
            rule_name=state.rule.name,
            metric=state.rule.metric,
            value=state.last_value,
            threshold=state.rule.threshold,
            severity=state.rule.severity,
            message=state.rule.summary or f"{state.rule.name} 触发",
            labels=dict(state.rule.labels),
        )
        self._logger.warning("alert_fired", rule=state.rule.name, value=state.last_value)
        await asyncio.gather(
            *(ch.deliver(payload) for ch in self._channels),
            return_exceptions=True,
        )
        bus = self._bus if self._bus is not None else get_event_bus()
        await bus.publish(EventType.ALERT_FIRED, payload, severity=state.rule.severity)

    async def _resolve(self, state: AlertState) -> None:
        """消解告警：投递通道 + 发事件。"""
        payload = AlertPayload(
            rule_name=state.rule.name,
            metric=state.rule.metric,
            value=state.last_value,
            threshold=state.rule.threshold,
            severity=state.rule.severity,
            message=f"{state.rule.name} 已恢复",
            labels=dict(state.rule.labels),
        )
        self._logger.info("alert_resolved", rule=state.rule.name, value=state.last_value)
        await asyncio.gather(
            *(ch.deliver(payload) for ch in self._channels),
            return_exceptions=True,
        )
        bus = self._bus if self._bus is not None else get_event_bus()
        await bus.publish(EventType.ALERT_RESOLVED, payload, severity=Severity.INFO)
