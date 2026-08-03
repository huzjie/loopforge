"""``http_fetch`` 工具 —— 受网络策略约束的 HTTP 抓取（``PROBEABLE``）。

网络策略
--------
* 仅允许 ``http`` / ``https``。
* 默认**禁止**私网 / 环回 / 链路本地地址（SSRF 防护），除非 ``allow_private=True``。
* 响应体上限 ``max_bytes``，超出截断。

幂等
----
带 ``idempotency_key`` 时，响应写入工作区 ``.loopforge/http_cache/<key>.json``，
probe 回查缓存（§8.1.5：``Idempotency-Key`` 回查）。不带 key 时按 ``UNSAFE``
语义处理：probe 返回 ``executed=False, confidence="unknown"``。
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
from typing import ClassVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from loopforge.core.enums import SideEffectClass
from loopforge.tools.base import BaseTool, ProbeResult, StepIntent, ToolContext, ToolResult

__all__ = ["HttpFetchArgs", "HttpFetchTool", "check_url_policy"]


class HttpFetchArgs(BaseModel):
    """HTTP 抓取参数。"""

    url: str = Field(min_length=1, max_length=4096)
    method: str = Field(default="GET", pattern="^(GET|HEAD|POST|PUT|PATCH|DELETE)$")
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    timeout_s: float = Field(default=15.0, gt=0, le=120)
    max_bytes: int = Field(default=262_144, ge=1024, le=8_388_608)
    allow_private: bool = Field(default=False, description="是否允许访问私网地址（默认禁）")
    idempotency_key: str | None = Field(default=None, max_length=128)


def check_url_policy(url: str, *, allow_private: bool = False) -> str | None:
    """检查 URL 是否满足网络策略。

    Args:
        url: 目标 URL。
        allow_private: 是否放行私网。

    Returns:
        ``None`` 表示放行；否则返回拒绝原因。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"仅允许 http/https，收到 {parsed.scheme!r}"
    if not parsed.hostname:
        return "URL 缺少主机名"
    try:
        host = parsed.hostname.lower().rstrip(".")
        if host in ("localhost", "127.0.0.1", "::1"):
            if not allow_private:
                return "禁止访问环回地址"
        else:
            addr_info = socket.getaddrinfo(host, parsed.port or 80)
            for family, _type, _proto, _canon, sockaddr in addr_info[:8]:
                try:
                    ip = ipaddress.ip_address(sockaddr[0])
                except ValueError:
                    continue
                if not allow_private and (
                    ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                ):
                    return f"禁止访问私网地址：{sockaddr[0]}"
    except OSError as exc:
        return f"DNS 解析失败：{exc}"
    return None


class HttpFetchTool(BaseTool):
    """抓取 HTTP 资源。"""

    name: ClassVar[str] = "http_fetch"
    description: ClassVar[str] = (
        "抓取 HTTP(S) 资源，受网络策略约束（默认禁止私网/环回，SSRF 防护）。"
        "带 idempotency_key 时响应会缓存到工作区，可幂等探测。"
    )
    args_schema: ClassVar[type[BaseModel]] = HttpFetchArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.PROBEABLE

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """执行抓取。"""
        parsed = args.model_dump()
        reason = check_url_policy(parsed["url"], allow_private=parsed["allow_private"])
        if reason:
            return ToolResult.failure(f"网络策略拒绝：{reason}", error_code="LF-POLICY-1001")

        headers = dict(parsed["headers"])
        if parsed["idempotency_key"]:
            headers.setdefault("Idempotency-Key", parsed["idempotency_key"])

        try:
            async with httpx.AsyncClient(timeout=parsed["timeout_s"], follow_redirects=True) as client:
                response = await client.request(
                    parsed["method"],
                    parsed["url"],
                    headers=headers,
                    content=parsed["body"].encode("utf-8") if parsed["body"] is not None else None,
                )
        except Exception as exc:  # noqa: BLE001 - 网络异常统一转 ToolResult
            return ToolResult.failure(
                f"请求失败：{exc}",
                error_code="LF-TOOL-0902",
            )

        body = response.text
        truncated = len(body.encode("utf-8")) > parsed["max_bytes"]
        if truncated:
            body = body.encode("utf-8")[: parsed["max_bytes"]].decode("utf-8", errors="replace")

        output = (
            f"HTTP {response.status_code} {response.reason_phrase}\n"
            f"URL: {parsed['url']}\n"
            f"Content-Type: {response.headers.get('content-type', '')}\n\n"
            f"{body}"
        )
        data = {
            "status_code": response.status_code,
            "url": parsed["url"],
            "content_type": response.headers.get("content-type", ""),
            "truncated": truncated,
        }
        external_ref = None
        if parsed["idempotency_key"]:
            external_ref = await self._cache_response(ctx, parsed["idempotency_key"], response)
            data["cache_path"] = external_ref
        return ToolResult(
            ok=response.status_code < 400,
            output=output[:4000],
            data=data,
            external_ref=external_ref,
            truncated=truncated,
            bytes_out=len(output.encode("utf-8")),
        )

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """按缓存回查（带 idempotency_key 时）。"""
        key = intent.idempotency_key or str(intent.args.get("idempotency_key", ""))
        if not key:
            return ProbeResult(
                executed=False,
                confidence="unknown",
                evidence="http_fetch 未带 idempotency_key，视为不可探测",
            )
        if ctx.executor is None or ctx.workspace is None:
            return ProbeResult(executed=False, confidence="low", evidence="executor 未注入")
        rel = self._cache_rel(key)
        try:
            content = await ctx.executor.read_file(ctx.workspace, rel)
        except Exception:  # noqa: BLE001
            return ProbeResult(executed=False, confidence="medium", evidence=f"缓存不存在：{rel}")
        return ProbeResult(
            executed=True,
            external_ref=f"local://{rel}",
            confidence="high",
            evidence=f"缓存存在：{rel}",
        )

    # ------------------------------------------------------------------ 内部

    def _cache_rel(self, key: str) -> str:
        """缓存相对路径。"""
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return f".loopforge/http_cache/{digest}.json"

    async def _cache_response(self, ctx: ToolContext, key: str, response: httpx.Response) -> str:
        """把响应写入工作区缓存（失败不阻断主流程）。"""
        rel = self._cache_rel(key)
        payload = {
            "idempotency_key": key,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "text": response.text[:262_144],
        }
        try:
            await ctx.executor.write_file(
                ctx.workspace, rel, json.dumps(payload, ensure_ascii=False)
            )
        except Exception:  # noqa: BLE001 - 缓存失败只降级为无 external_ref
            return None  # type: ignore[return-value]
        return f"local://{rel}"
