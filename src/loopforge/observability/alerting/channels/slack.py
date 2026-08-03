"""Slack 告警通道 —— Incoming Webhook。"""

from __future__ import annotations

from typing import Any

import httpx

from loopforge.core.event_types import AlertPayload
from loopforge.core.logging import get_logger

from loopforge.observability.alerting.channels.base import AlertChannel

__all__ = ["SlackChannel"]

_logger = get_logger(__name__)


class SlackChannel(AlertChannel):
    """把告警发到 Slack Incoming Webhook。"""

    kind = "slack"

    def __init__(self, webhook_url: str, *, channel: str | None = None, timeout_s: float = 10.0) -> None:
        self.webhook_url = webhook_url
        self.channel = channel
        self.timeout_s = timeout_s

    async def deliver(self, payload: AlertPayload) -> bool:
        body: dict[str, Any] = {
            "text": f"[{payload.severity}] {payload.message}",
        }
        if self.channel:
            body["channel"] = self.channel
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(self.webhook_url, json=body)
                return resp.status_code < 400
        except Exception as exc:  # noqa: BLE001
            _logger.warning("slack_channel_failed", error=str(exc))
            return False
