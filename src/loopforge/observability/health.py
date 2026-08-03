"""健康检查 —— ``/healthz`` / ``/readyz`` / ``/health/detail`` 的底层逻辑。

``/healthz`` 只测进程存活（恒 true）；``/readyz`` 检查 DB / Provider / Executor
三项依赖；``/health/detail`` 返回逐项状态与耗时（V 权限）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loopforge import __version__
from loopforge.core.clock import get_clock, to_iso8601
from loopforge.core.enums import Severity
from loopforge.core.logging import get_logger
from loopforge.db.engine import ping
from loopforge.schemas.common import HealthResponse, HealthStatus

__all__ = [
    "HealthCheckResult",
    "check_db",
    "check_provider",
    "check_executor",
    "check_dependencies",
    "health_response",
]

_logger = get_logger(__name__)


@dataclass
class HealthCheckResult:
    """单项健康检查结果。"""

    name: str
    ok: bool = True
    latency_ms: float = 0.0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转字典（供 detail 端点）。"""
        return {
            "name": self.name,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 2),
            "detail": self.detail,
        }


async def check_db() -> HealthCheckResult:
    """数据库连通性检查。"""
    import time

    start = time.perf_counter()
    ok = await ping()
    latency = (time.perf_counter() - start) * 1000
    return HealthCheckResult(name="db", ok=ok, latency_ms=latency, detail="ok" if ok else "ping failed")


async def check_provider() -> HealthCheckResult:
    """Provider 可用性检查（尽力而为；无 Provider 视为降级而非失败）。"""
    try:
        from loopforge.providers import create_mock_provider

        provider = create_mock_provider()
        health = await provider.health_check()
        return HealthCheckResult(
            name="provider",
            ok=health.ok,
            latency_ms=float(health.latency_ms or 0.0),
            detail=health.message or ("" if health.ok else "provider unhealthy"),
        )
    except Exception as exc:  # noqa: BLE001
        return HealthCheckResult(name="provider", ok=False, detail=str(exc))


async def check_executor() -> HealthCheckResult:
    """Executor 可用性检查（尽力而为）。"""
    try:
        from loopforge.executors import create_executor
        from loopforge.core.config import get_settings

        executor = create_executor(get_settings().executor)
        health = await executor.healthcheck()
        return HealthCheckResult(
            name="executor",
            ok=health.ok,
            latency_ms=float(health.latency_ms or 0.0),
            detail="ok" if health.ok else health.message,
        )
    except Exception as exc:  # noqa: BLE001
        return HealthCheckResult(name="executor", ok=False, detail=str(exc))


async def check_dependencies() -> list[HealthCheckResult]:
    """并行检查全部依赖。"""
    import asyncio

    results = await asyncio.gather(
        check_db(),
        check_provider(),
        check_executor(),
    )
    return list(results)


def health_response(
    checks: list[HealthCheckResult],
    *,
    detail: bool = False,
) -> HealthResponse:
    """把检查结果组装成 ``HealthResponse``。

    :param checks: 各项检查结果。
    :param detail: 是否要求全部健康（readyz 用）；否则只要求 DB 健康。
    """
    check_map = {item.name: item.ok for item in checks}
    if detail:
        # readyz：三项都过才算 ready
        ready = all(item.ok for item in checks)
        return HealthResponse(
            status=HealthStatus.HEALTHY if ready else HealthStatus.UNHEALTHY,
            version=__version__,
            checks=check_map,
            severity=Severity.INFO if ready else Severity.CRITICAL,
            checked_at=get_clock().now(),
        )
    # healthz：只看 DB
    db_ok = check_map.get("db", True)
    return HealthResponse(
        status=HealthStatus.HEALTHY if db_ok else HealthStatus.UNHEALTHY,
        version=__version__,
        checks=check_map,
        severity=Severity.INFO if db_ok else Severity.CRITICAL,
        checked_at=get_clock().now(),
    )
