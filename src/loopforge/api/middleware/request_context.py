"""请求上下文中间件 —— 生成 / 透传 ``X-Trace-Id`` 并注入 ``request.state``。

所有日志与响应都带 ``trace_id``，前端排障靠它（§10.5 结构化日志字段）。
"""

from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from loopforge.constants import HEADER_TRACE_ID
from loopforge.core.ids import new_trace_id
from loopforge.core.logging import bind_context, clear_context, log_context

__all__ = ["RequestContextMiddleware"]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求建立独立上下文。"""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        trace_id = request.headers.get(HEADER_TRACE_ID) or new_trace_id()
        trace_id = trace_id[:64]
        request.state.trace_id = trace_id
        with log_context(trace_id=trace_id, method=request.method, path=request.url.path):
            response = await call_next(request)
        response.headers[HEADER_TRACE_ID] = trace_id
        return response
