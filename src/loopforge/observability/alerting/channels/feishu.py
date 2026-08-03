"""飞书告警通道 —— 自定义机器人 Webhook。"""

from __future__ import annotations

from typing import Any

import httpx

from loopforge.core.event_types import AlertPayload
from loopforge.core.logging import get_logger

from loopforge.observability.alerting.channels.base import AlertChannel

__all__ = ["FeishuChannel"]

_logger = get_logger(__name__)


class FeishuChannel(AlertChannel):
    """把告警发到飞书自定义机器人。"""

    kind = "feishu"

    def __init__(self, webhook_url: str, *, timeout_s: float = 10.0) -> None:
        self.webhook_url = webhook_url
        self.timeout_s = timeout_s

    async def deliver(self, payload: AlertPayload) -> bool:
        body: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": f"[LoopForge] [{payload.severity}] {payload.message}"},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(self.webhook_url, json=body)
                return resp.status_code < 400
        except Exception as exc:  # noqa: BLE001
            _logger.warning("feishu_channel_failed", error=str(exc))
            return False
