"""访问日志中间件 —— 每个请求一条结构化日志。

记录：方法 / 路径 / 状态码 / 耗时 / trace_id / 客户端 IP。
同时打点 Prometheus ``loopforge_http_requests_total`` 与 ``loopforge_http_duration_seconds``。
"""

from __future__ import annotations

import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from loopforge.core.logging import get_logger
from loopforge.observability.metrics import (
    HTTP_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
)

__all__ = ["AccessLogMiddleware"]

_logger = get_logger(__name__)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """请求级访问日志 + HTTP 指标。"""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        start = time.perf_counter()
        method = request.method
        path = request.url.path
        response = await call_next(request)
        duration = time.perf_counter() - start
        status = response.status_code
        trace_id = str(getattr(request.state, "trace_id", ""))
        _logger.info(
            "http_request",
            method=method,
            path=path,
            status=status,
            duration_ms=round(duration * 1000, 2),
            trace_id=trace_id,
            client_ip=request.client.host if request.client else "",
        )
        try:
            HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status=str(status)).inc()
            HTTP_DURATION_SECONDS.labels(method=method, path=path).observe(duration)
        except Exception:  # noqa: BLE001 —— 指标打点失败不影响请求
            pass
        return response
