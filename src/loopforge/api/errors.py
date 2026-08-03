"""API 统一响应信封与全局异常处理。

契约（``TASK_BREAKDOWN.md`` §11.8）::

    成功: { "code": "OK", "data": {...}, "message": "", "trace_id": "01J..." }
    失败: { "code": "LF-RUN-0301", "data": null, "message": "Run 不存在", "trace_id": "01J..." }
    分页: { "code": "OK", "data": { "items": [], "next_cursor": "...", "has_more": true, "total": 123 }, ... }

唯一例外：``/metrics``（Prometheus 文本）与文件下载端点。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from loopforge.core.errors import LoopForgeError, wrap_exception
from loopforge.core.ids import new_trace_id
from loopforge.core.logging import get_logger

__all__ = [
    "ApiResponse",
    "ApiPage",
    "ok",
    "page",
    "error_response",
    "register_exception_handlers",
    "extract_trace_id",
]

_logger = get_logger(__name__)


class ApiResponse(BaseModel):
    """统一响应信封。"""

    model_config = ConfigDict(extra="forbid")

    code: str = "OK"
    data: Any = None
    message: str = ""
    trace_id: str = ""


class ApiPage(BaseModel):
    """分页响应固定形状（§11.8）。"""

    model_config = ConfigDict(extra="forbid")

    items: list[Any] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    total: int = 0


def ok(data: Any = None, *, message: str = "", trace_id: str | None = None) -> ApiResponse:
    """构造成功响应。"""
    return ApiResponse(code="OK", data=data, message=message, trace_id=trace_id or "")


def page(
    items: list[Any],
    *,
    total: int,
    next_cursor: str | None = None,
    has_more: bool | None = None,
    trace_id: str | None = None,
) -> ApiResponse:
    """构造分页响应。"""
    return ApiResponse(
        code="OK",
        data=ApiPage(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more if has_more is not None else bool(next_cursor),
            total=total,
        ),
        trace_id=trace_id or "",
    )


def error_response(
    *,
    code: str,
    message: str,
    trace_id: str,
    details: dict[str, Any] | None = None,
    http_status: int = 500,
) -> JSONResponse:
    """构造统一错误 JSON 响应。"""
    data: dict[str, Any] = {"details": details} if details else None
    return JSONResponse(
        status_code=http_status,
        content={
            "code": code,
            "data": data,
            "message": message,
            "trace_id": trace_id,
        },
    )


def extract_trace_id(request: Request) -> str:
    """从请求中取出 trace_id（优先头，其次新建）。"""
    header = request.headers.get("X-Trace-Id")
    if header:
        return header[:64]
    return new_trace_id()


def _trace_from_request(request: Request) -> str:
    """读取中间件注入的 trace_id。"""
    return str(getattr(request.state, "trace_id", "") or extract_trace_id(request))


def register_exception_handlers(app: FastAPI) -> None:
    """把全部异常处理器挂到 FastAPI 应用上。"""

    @app.exception_handler(LoopForgeError)
    async def _loopforge_handler(request: Request, exc: LoopForgeError) -> JSONResponse:
        trace_id = _trace_from_request(request)
        _logger.warning(
            "loopforge_error",
            code=exc.code,
            http_status=exc.http_status,
            trace_id=trace_id,
            details=exc.details,
            error=str(exc),
        )
        return error_response(
            code=exc.code,
            message=exc.message,
            trace_id=trace_id,
            details=exc.details or None,
            http_status=exc.http_status,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        trace_id = _trace_from_request(request)
        _logger.warning(
            "request_validation_error",
            trace_id=trace_id,
            errors=exc.errors()[:20],
        )
        return error_response(
            code="LF-API-1501",
            message="请求参数校验失败",
            trace_id=trace_id,
            details={"errors": exc.errors()[:20]},
            http_status=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        trace_id = _trace_from_request(request)
        return error_response(
            code=f"LF-API-{exc.status_code}",
            message=str(exc.detail) or "HTTP 错误",
            trace_id=trace_id,
            http_status=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        trace_id = _trace_from_request(request)
        wrapped = wrap_exception(exc, message="服务器内部错误")
        _logger.exception(
            "unhandled_exception",
            trace_id=trace_id,
            code=wrapped.code,
            exc_info=exc,
        )
        return error_response(
            code=wrapped.code,
            message=wrapped.message,
            trace_id=trace_id,
            details=wrapped.details or None,
            http_status=wrapped.http_status,
        )
