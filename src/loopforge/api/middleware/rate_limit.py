"""速率限制中间件 —— 令牌桶（进程内）。

按客户端 IP 限流，超过 ``api.rate_limit_per_minute`` 返回 ``429 LF-API-1502``。
生产多副本部署时应替换为 Redis 分布式实现；本实现满足自托管单实例场景。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from loopforge.core.config import get_settings
from loopforge.core.logging import get_logger

__all__ = ["RateLimitMiddleware"]

_logger = get_logger(__name__)


class _TokenBucket:
    """简单令牌桶。"""

    __slots__ = ("capacity", "tokens", "rate", "updated_at")

    def __init__(self, capacity: float, rate: float) -> None:
        self.capacity = capacity
        self.tokens = capacity
        self.rate = rate
        self.updated_at = time.monotonic()

    def take(self) -> bool:
        """尝试取一个令牌。"""
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated_at) * self.rate)
        self.updated_at = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按 IP 限流。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._buckets: dict[str, _TokenBucket] = defaultdict(
            lambda: _TokenBucket(
                capacity=float(get_settings().api.rate_limit_per_minute),
                rate=float(get_settings().api.rate_limit_per_minute) / 60.0,
            )
        )
        self._lock = threading.Lock()

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        # 健康检查与指标不受限流（探针与抓取频率可能很高）。
        if request.url.path in {"/healthz", "/readyz", "/metrics"}:
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        with self._lock:
            bucket = self._buckets[client_ip]
            allowed = bucket.take()
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "code": "LF-API-1502",
                    "data": None,
                    "message": "请求过于频繁，请稍后重试",
                    "trace_id": str(getattr(request.state, "trace_id", "")),
                },
            )
        return await call_next(request)
