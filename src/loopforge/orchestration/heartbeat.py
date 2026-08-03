"""心跳循环 —— 后台续租，租约丢失时触发 Worker 自我熔断。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1 / §11.6。

设计要点
--------
* 一个 :class:`HeartbeatLoop` 绑定一条租约，周期性调用
  :meth:`LeaseManager.heartbeat`；
* 心跳失败（:class:`~loopforge.core.errors.LeaseLostError` / 
  :class:`~loopforge.core.errors.FencedOutError`）时调用 ``on_lost`` 回调，
  让 Worker 停止当前 Task 的一切副作用、写 ``WORKER_FENCED_OUT`` 事件、请求下
  一个任务 —— **自我熔断**，而不是把错误吞掉继续干活；
* ``stop()`` 幂等，可重复调用。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loopforge.core.clock import Clock, get_clock
from loopforge.core.errors import FencedOutError, LeaseLostError, LoopForgeError
from loopforge.core.logging import get_logger
from loopforge.orchestration.lease_manager import LeaseManager
from loopforge.models.lease import Lease

__all__ = ["HeartbeatLoop"]

_LOG = get_logger(__name__)

#: 心跳丢失回调类型：接收异常。
LostHandler = Callable[[LoopForgeError], Awaitable[None] | None]


class HeartbeatLoop:
    """后台续租循环。

    :param lease_manager: 租约管理器。
    :param lease: 要守护的租约。
    :param interval_s: 心跳间隔。
    :param clock: 时间源。
    :param on_lost: 心跳丢失回调（Worker 自我熔断钩子）。
    :param progress_provider: 可选的进度提示回调，每次心跳调用取 ``str``。
    """

    def __init__(
        self,
        *,
        lease_manager: LeaseManager,
        lease: Lease,
        interval_s: float,
        clock: Clock | None = None,
        on_lost: LostHandler | None = None,
        progress_provider: Callable[[], str] | None = None,
    ) -> None:
        self._lm: LeaseManager = lease_manager
        self._lease: Lease = lease
        self._interval_s: float = float(interval_s)
        self._clock: Clock = clock or get_clock()
        self._on_lost: LostHandler | None = on_lost
        self._progress_provider: Callable[[], str] | None = progress_provider
        self._task: asyncio.Task[Any] | None = None
        self._stopped: bool = False
        self._lost: LoopForgeError | None = None

    # -- 生命周期 -----------------------------------------------------------

    def start(self) -> None:
        """启动后台心跳任务（幂等）。"""
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run(), name="heartbeat-loop")

    async def stop(self) -> None:
        """停止心跳任务（幂等，可重复调用）。"""
        self._stopped = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    @property
    def lost_error(self) -> LoopForgeError | None:
        """心跳是否已丢失，以及丢失时的异常。"""
        return self._lost

    @property
    def is_lost(self) -> bool:
        """是否已判定租约丢失。"""
        return self._lost is not None

    async def wait_lost(self, timeout_s: float = 30.0) -> LoopForgeError | None:
        """等待心跳丢失信号（Worker 熔断阻塞点）。

        :param timeout_s: 等待超时。
        :returns: 丢失异常；超时返回 ``None``。
        """
        started = self._clock.monotonic()
        while self._lost is None and not self._stopped:
            if self._clock.monotonic() - started > timeout_s:
                return None
            await asyncio.sleep(0.05)
        return self._lost

    # -- 内部 ---------------------------------------------------------------

    async def _run(self) -> None:
        """心跳主循环。"""
        while not self._stopped:
            await asyncio.sleep(self._interval_s)
            if self._stopped:
                break
            try:
                hint = self._progress_provider() if self._progress_provider else None
                await self._lm.heartbeat(
                    lease_id=self._lease.id,
                    fence_token=self._lease.fence_token,
                    progress_hint=hint,
                )
            except (LeaseLostError, FencedOutError) as exc:
                self._lost = exc
                _LOG.warning(
                    "lease_lost",
                    lease_id=self._lease.id,
                    fence_token=self._lease.fence_token,
                    error_code=exc.code,
                )
                if self._on_lost is not None:
                    try:
                        await self._on_lost(exc)
                    except Exception as nested:  # noqa: BLE001
                        _LOG.error("heartbeat_on_lost_failed", error=str(nested))
                return
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 - 心跳不应拖垮 Worker
                _LOG.warning(
                    "heartbeat_transient_error",
                    lease_id=self._lease.id,
                    error=str(exc),
                )
