"""写操作幂等 —— ``Idempotency-Key`` 头支持。

契约（§9.3 约定 3）：所有 POST/PUT/DELETE 支持 ``Idempotency-Key`` 头，
重复请求返回首次结果（存 ``idempotency_records`` 表）。这是前端重试与网络抖动的兜底。

实现：中间件在请求进入路由前查幂等表；命中则直接返回首次响应，
未命中则放行并把首次响应落库。Key = ``sha256(method:path:Idempotency-Key)``，
同时校验请求体指纹，同 Key 不同 Body 返回 ``LF-API-1503``。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from loopforge.constants import HEADER_IDEMPOTENCY_KEY
from loopforge.core.clock import get_clock
from loopforge.core.errors import ApiError
from loopforge.core.hashing import canonical_json, sha256_hex
from loopforge.core.logging import get_logger
from loopforge.db.repositories.step_repo import IdempotencyRepository
from loopforge.db.session import get_session_factory

__all__ = ["IdempotencyService", "IdempotencyMiddleware"]

_logger = get_logger(__name__)

#: 幂等记录保留时长（窗口内重复请求都能命中）。
IDEMPOTENCY_TTL_S: int = 24 * 3600

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class IdempotencyService:
    """幂等记录的读写封装。"""

    def __init__(self, session_factory: Any | None = None) -> None:
        self._session_factory = session_factory

    async def lookup(self, key: str) -> dict[str, Any] | None:
        """按 key 查幂等记录；命中返回快照，未命中返回 None。"""
        factory = self._session_factory or get_session_factory()
        async with factory() as session:
            repo = IdempotencyRepository(session)
            record = await repo.get(key)
            if record is None:
                return None
            return {
                "key": record.key,
                "request_fingerprint": record.request_fingerprint,
                "response_snapshot": record.response_snapshot,
                "note": record.note,
            }

    async def store(
        self,
        *,
        key: str,
        request_fingerprint: str,
        response_snapshot: dict[str, Any],
        note: str = "api",
    ) -> None:
        """写入幂等记录（幂等 upsert）。"""
        factory = self._session_factory or get_session_factory()
        now = get_clock().now()
        async with factory() as session:
            repo = IdempotencyRepository(session)
            await repo.put_if_absent(
                key=key,
                scope="api",
                request_fingerprint=request_fingerprint,
                response_snapshot=response_snapshot,
                expires_at=now + timedelta(seconds=IDEMPOTENCY_TTL_S),
                note=note,
            )
            await session.commit()


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """写请求幂等中间件。"""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        """同 Key 并发时串行化（进程内），避免双写。"""
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        idem_key = request.headers.get(HEADER_IDEMPOTENCY_KEY)
        method = request.method.upper()
        if method not in _MUTATING_METHODS or not idem_key:
            return await call_next(request)

        key = sha256_hex(f"{method}:{request.url.path}:{idem_key}")
        service = IdempotencyService()
        try:
            await request.body()
        except Exception:  # noqa: BLE001 —— body 读取失败按无 body 处理
            pass
        body = getattr(request, "_body", b"")
        fingerprint = sha256_hex(canonical_json({"method": method, "body": body.decode("utf-8", "replace")}))

        lock = self._lock_for(key)
        async with lock:
            existing = await service.lookup(key)
            if existing is not None:
                if existing.get("request_fingerprint") != fingerprint:
                    return JSONResponse(
                        status_code=409,
                        content={
                            "code": "LF-API-1503",
                            "data": None,
                            "message": "幂等键冲突：同一 Idempotency-Key 请求体不一致",
                            "trace_id": str(getattr(request.state, "trace_id", "")),
                        },
                    )
                snapshot = existing.get("response_snapshot") or {}
                return JSONResponse(
                    status_code=int(snapshot.get("status_code", 200)),
                    content=snapshot.get("body", {}),
                )

            response = await call_next(request)
            if response.status_code < 500:
                resp_body = b""
                try:
                    chunks = [chunk async for chunk in response.body_iterator]
                    resp_body = b"".join(chunks)
                except Exception:  # noqa: BLE001 —— 流式响应无法缓存则跳过
                    pass
                body_dict: dict[str, Any] = {}
                try:
                    import json

                    body_dict = json.loads(resp_body.decode("utf-8"))
                except Exception:  # noqa: BLE001 —— 非 JSON 响应不缓存
                    pass
                await service.store(
                    key=key,
                    request_fingerprint=fingerprint,
                    response_snapshot={
                        "status_code": response.status_code,
                        "body": body_dict,
                    },
                )
                return JSONResponse(status_code=response.status_code, content=body_dict)
            return response
