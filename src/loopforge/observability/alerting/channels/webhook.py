"""通用 Webhook 告警通道。"""

from __future__ import annotations

from typing import Any

import httpx

from loopforge.core.event_types import AlertPayload
from loopforge.core.logging import get_logger

from loopforge.observability.alerting.channels.base import AlertChannel

__all__ = ["WebhookChannel"]

_logger = get_logger(__name__)


class WebhookChannel(AlertChannel):
    """把告警 POST 到任意 Webhook URL。"""

    kind = "webhook"

    def __init__(self, url: str, *, headers: dict[str, str] | None = None, timeout_s: float = 10.0) -> None:
        self.url = url
        self.headers = headers or {"Content-Type": "application/json"}
        self.timeout_s = timeout_s

    async def deliver(self, payload: AlertPayload) -> bool:
        body: dict[str, Any] = {
            "text": payload.message,
            "rule_name": payload.rule_name,
            "metric": payload.metric,
            "value": payload.value,
            "threshold": payload.threshold,
            "severity": str(payload.severity),
            "labels": payload.labels,
            "fired_at": payload.fired_at.isoformat() if payload.fired_at else None,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(self.url, json=body, headers=self.headers)
                return resp.status_code < 400
        except Exception as exc:  # noqa: BLE001
            _logger.warning("webhook_channel_failed", error=str(exc), url=self.url)
            return False
