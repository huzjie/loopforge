"""进程内异步事件总线（``TASK_BREAKDOWN`` §11.2 冻结签名）。

职责边界：

* **只负责进程内扇出**，不负责落库。落库通过 ``persist_hook`` 注入
  （WP7 的 ``event_service`` 会把它接到 ``event_repo.append()``）；
* **带背压**：队列满时按 ``overflow_policy`` 丢弃或阻塞，绝不无限膨胀内存；
* **订阅者异常不影响总线**：handler 抛错只记账，不打断其他订阅者。

冻结签名::

    async def publish(self, event_type, payload, *, run_id=None, task_id=None,
                      severity=Severity.INFO) -> int      # 返回 seq
    def subscribe(self, handler, *, types=None) -> str    # 返回 subscription_id
    def unsubscribe(self, subscription_id) -> None
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel

from loopforge.core.clock import Clock, get_clock
from loopforge.core.enums import Severity
from loopforge.core.event_types import EventType, validate_payload
from loopforge.core.ids import new_ulid

__all__ = [
    "EventEnvelope",
    "EventBus",
    "EventBusStats",
    "OverflowPolicy",
    "get_event_bus",
    "set_event_bus",
]

OverflowPolicy = Literal["drop_oldest", "drop_newest", "block"]

#: 默认队列容量，与 ``config.orchestration.event_bus_queue_size`` 对齐。
DEFAULT_QUEUE_SIZE: Final[int] = 10_000

EventHandler = Callable[["EventEnvelope"], Awaitable[None]]
PersistHook = Callable[["EventEnvelope"], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """事件信封 —— 总线内流转的唯一载体。

    Attributes:
        seq: 进程内单调递增序号，从 1 开始。
        id: 事件 ULID。
        event_type: 事件类型。
        payload: 已经过 ``EVENT_PAYLOAD_MAP`` 校验的 payload 模型。
        severity: 严重级。
        run_id: 关联 Run（可空）。
        task_id: 关联 Task（可空）。
        step_id: 关联 Step（可空）。
        trace_id: 链路追踪 ID（可空）。
        created_at: 事件产生时刻（tz-aware UTC）。
    """

    seq: int
    id: str
    event_type: EventType
    payload: BaseModel
    severity: Severity
    run_id: str | None
    task_id: str | None
    step_id: str | None
    trace_id: str | None
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """转成可 JSON 化的字典（落库与 WebSocket 推送共用）。

        Returns:
            纯 Python 字典。
        """
        return {
            "seq": self.seq,
            "id": self.id,
            "type": str(self.event_type),
            "severity": str(self.severity),
            "run_id": self.run_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "trace_id": self.trace_id,
            "payload": self.payload.model_dump(mode="json"),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class EventBusStats:
    """总线运行计数（可观测与测试断言用）。

    Attributes:
        published: 累计成功入队的事件数。
        delivered: 累计成功投递给订阅者的次数（一个事件 N 个订阅者算 N 次）。
        dropped: 因背压丢弃的事件数。
        handler_errors: 订阅者抛出异常的次数。
        persist_errors: 持久化钩子抛出异常的次数。
    """

    published: int = 0
    delivered: int = 0
    dropped: int = 0
    handler_errors: int = 0
    persist_errors: int = 0

    def snapshot(self) -> dict[str, int]:
        """导出计数快照。

        Returns:
            指标字典。
        """
        return {
            "published": self.published,
            "delivered": self.delivered,
            "dropped": self.dropped,
            "handler_errors": self.handler_errors,
            "persist_errors": self.persist_errors,
        }


@dataclass(slots=True)
class _Subscription:
    """一个订阅者。

    Attributes:
        id: 订阅 ID。
        handler: 异步回调。
        types: 关心的事件类型集合；``None`` 表示订阅全部。
    """

    id: str
    handler: EventHandler
    types: frozenset[EventType] | None = None

    def matches(self, event_type: EventType) -> bool:
        """判断该订阅是否关心某事件类型。

        Args:
            event_type: 事件类型。

        Returns:
            关心返回 ``True``。
        """
        return self.types is None or event_type in self.types


class EventBus:
    """进程内异步发布订阅总线。

    生命周期::

        bus = EventBus()
        await bus.start()          # 启动后台分发任务
        await bus.publish(...)     # 发布
        await bus.drain()          # 等待队列排空（测试常用）
        await bus.stop()           # 优雅停机

    未调用 :meth:`start` 时 :meth:`publish` 依然可用（事件进队列），
    调用 :meth:`drain` 会就地把积压事件分发完毕。
    """

    def __init__(
        self,
        *,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        overflow_policy: OverflowPolicy = "drop_oldest",
        persist_hook: PersistHook | None = None,
        clock: Clock | None = None,
    ) -> None:
        """初始化总线。

        Args:
            queue_size: 背压队列容量，必须为正。
            overflow_policy: 队列满时的策略。
            persist_hook: 落库钩子；在事件入队**之前**同步 await，
                保证「先落库再扇出」，崩溃时不丢事件。
            clock: 时钟依赖。

        Raises:
            ValueError: ``queue_size`` 非正。
        """
        if queue_size < 1:
            raise ValueError(f"queue_size 必须 >= 1，收到 {queue_size}")
        self._queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=queue_size)
        self._overflow_policy: OverflowPolicy = overflow_policy
        self._persist_hook: PersistHook | None = persist_hook
        self._clock: Clock = clock or get_clock()
        self._subscriptions: dict[str, _Subscription] = {}
        self._seq: int = 0
        self._seq_lock = asyncio.Lock()
        self._worker: asyncio.Task[None] | None = None
        self._running: bool = False
        self._stats = EventBusStats()

    # ------------------------------------------------------------------ 属性

    @property
    def stats(self) -> EventBusStats:
        """运行计数。"""
        return self._stats

    @property
    def running(self) -> bool:
        """后台分发任务是否在跑。"""
        return self._running

    @property
    def queue_size(self) -> int:
        """当前积压事件数。"""
        return self._queue.qsize()

    @property
    def subscription_count(self) -> int:
        """当前订阅者数量。"""
        return len(self._subscriptions)

    def set_persist_hook(self, hook: PersistHook | None) -> None:
        """设置或清除持久化钩子。

        Args:
            hook: 异步落库回调；``None`` 表示清除。
        """
        self._persist_hook = hook

    # ------------------------------------------------------------------ 订阅

    def subscribe(
        self,
        handler: EventHandler,
        *,
        types: Sequence[EventType] | None = None,
    ) -> str:
        """注册订阅者。

        Args:
            handler: 异步回调 ``(EventEnvelope) -> Awaitable[None]``。
            types: 关心的事件类型；``None`` 表示订阅全部。

        Returns:
            订阅 ID，用于 :meth:`unsubscribe`。

        Raises:
            TypeError: ``handler`` 不是可调用对象。
        """
        if not callable(handler):
            raise TypeError("handler 必须是可调用对象")
        subscription_id = new_ulid(clock=self._clock)
        self._subscriptions[subscription_id] = _Subscription(
            id=subscription_id,
            handler=handler,
            types=frozenset(types) if types is not None else None,
        )
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> None:
        """注销订阅者。

        Args:
            subscription_id: :meth:`subscribe` 返回的 ID。不存在时静默忽略，
                保证幂等（重复注销不应报错）。
        """
        self._subscriptions.pop(subscription_id, None)

    # ------------------------------------------------------------------ 发布

    async def publish(
        self,
        event_type: EventType,
        payload: BaseModel,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        severity: Severity = Severity.INFO,
        step_id: str | None = None,
        trace_id: str | None = None,
    ) -> int:
        """发布一个事件。

        流程：**校验 payload → 分配 seq → 落库钩子 → 入队**。
        任何一步失败都不会产生「有 seq 没落库」的空洞。

        Args:
            event_type: 事件类型，必须在 :class:`EventType` 中。
            payload: payload 模型实例，会用 ``EVENT_PAYLOAD_MAP`` 强制校验。
            run_id: 关联 Run。
            task_id: 关联 Task。
            severity: 严重级。
            step_id: 关联 Step（扩展参数，冻结签名之外的可选项）。
            trace_id: 链路 ID（扩展参数）。

        Returns:
            该事件的进程内序号 ``seq``（从 1 开始单调递增）。

        Raises:
            ValidationError: payload 与事件类型不匹配。
            asyncio.QueueFull: ``overflow_policy="block"`` 且总线已停机。
        """
        validated = validate_payload(event_type, payload)
        async with self._seq_lock:
            self._seq += 1
            seq = self._seq
        envelope = EventEnvelope(
            seq=seq,
            id=new_ulid(clock=self._clock),
            event_type=event_type,
            payload=validated,
            severity=severity,
            run_id=run_id,
            task_id=task_id,
            step_id=step_id,
            trace_id=trace_id,
            created_at=self._clock.now(),
        )
        if self._persist_hook is not None:
            try:
                await self._persist_hook(envelope)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 —— 落库失败不能拖垮业务主流程
                self._stats.persist_errors += 1
        await self._enqueue(envelope)
        return seq

    async def _enqueue(self, envelope: EventEnvelope) -> None:
        """按背压策略把信封放进队列。

        Args:
            envelope: 事件信封。
        """
        if self._overflow_policy == "block":
            await self._queue.put(envelope)
            self._stats.published += 1
            return
        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            if self._overflow_policy == "drop_newest":
                self._stats.dropped += 1
                return
            # drop_oldest：丢一条最旧的再放新的。
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._stats.dropped += 1
            except asyncio.QueueEmpty:  # pragma: no cover —— 竞态下的极端分支
                pass
            try:
                self._queue.put_nowait(envelope)
            except asyncio.QueueFull:  # pragma: no cover —— 仍然满则只能丢
                self._stats.dropped += 1
                return
        self._stats.published += 1

    # ------------------------------------------------------------------ 分发

    async def _dispatch(self, envelope: EventEnvelope) -> None:
        """把一个信封扇出给所有匹配的订阅者。

        Args:
            envelope: 事件信封。
        """
        targets = [
            sub for sub in list(self._subscriptions.values()) if sub.matches(envelope.event_type)
        ]
        if not targets:
            return
        results = await asyncio.gather(
            *(sub.handler(envelope) for sub in targets), return_exceptions=True
        )
        for outcome in results:
            if isinstance(outcome, BaseException):
                self._stats.handler_errors += 1
            else:
                self._stats.delivered += 1

    async def _run(self) -> None:
        """后台分发循环。"""
        while self._running:
            try:
                envelope = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                await self._dispatch(envelope)
            finally:
                self._queue.task_done()

    async def start(self) -> None:
        """启动后台分发任务（幂等）。"""
        if self._running:
            return
        self._running = True
        self._worker = asyncio.create_task(self._run(), name="loopforge-event-bus")

    async def stop(self, *, drain: bool = True, timeout_s: float = 5.0) -> None:
        """优雅停机。

        复用 :meth:`drain` 的语义，因此**未启动后台任务时也能把积压事件投递完**。
        早期实现直接 ``await self._queue.join()``：没有消费者时永远等不到
        ``task_done()``，只能超时并静默丢弃全部积压事件——那不叫优雅停机。

        Args:
            drain: 是否先把队列排空再停。
            timeout_s: 排空的最长等待秒数。
        """
        if drain:
            try:
                await self.drain(timeout_s=timeout_s)
            except TimeoutError:
                pass
        self._running = False
        worker = self._worker
        self._worker = None
        if worker is not None and not worker.done():
            worker.cancel()
            try:
                await worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 —— 停机吞掉一切
                pass

    async def drain(self, *, timeout_s: float | None = None) -> None:
        """等待队列中所有事件分发完毕。

        未启动后台任务时，会就地同步分发完积压事件（单测友好）。

        Args:
            timeout_s: 最长等待秒数；``None`` 表示无限等待。

        Raises:
            TimeoutError: 超时仍未排空。
        """
        if not self._running:
            while not self._queue.empty():
                envelope = self._queue.get_nowait()
                try:
                    await self._dispatch(envelope)
                finally:
                    self._queue.task_done()
            return
        if timeout_s is None:
            await self._queue.join()
        else:
            await asyncio.wait_for(self._queue.join(), timeout=timeout_s)

    async def __aenter__(self) -> EventBus:
        """支持 ``async with EventBus() as bus:``。

        Returns:
            自身。
        """
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """退出上下文时优雅停机。

        Args:
            *exc: 异常三元组（未使用）。
        """
        await self.stop()

    def __repr__(self) -> str:
        """调试友好表示。"""
        return (
            f"EventBus(running={self._running}, queued={self._queue.qsize()}, "
            f"subs={len(self._subscriptions)}, seq={self._seq})"
        )


# ---------------------------------------------------------------------------
# 进程级默认实例
# ---------------------------------------------------------------------------

_default_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """取得进程级默认总线（懒加载）。

    Returns:
        默认 :class:`EventBus` 实例。
    """
    global _default_bus  # noqa: PLW0603 —— 单例模式必须
    if _default_bus is None:
        _default_bus = EventBus()
    return _default_bus


def set_event_bus(bus: EventBus | None) -> None:
    """替换或清空进程级默认总线（测试与启动装配用）。

    Args:
        bus: 新总线；``None`` 表示清空。
    """
    global _default_bus  # noqa: PLW0603 —— 单例模式必须
    _default_bus = bus


@dataclass(slots=True)
class RecordingSubscriber:
    """把事件收进列表的订阅者 —— 单测辅助件。

    Attributes:
        events: 已接收的信封列表。
    """

    events: list[EventEnvelope] = field(default_factory=list)

    async def __call__(self, envelope: EventEnvelope) -> None:
        """接收一个事件。

        Args:
            envelope: 事件信封。
        """
        self.events.append(envelope)

    def types(self) -> list[EventType]:
        """已接收事件的类型序列。

        Returns:
            类型列表，保持接收顺序。
        """
        return [item.event_type for item in self.events]

    def clear(self) -> None:
        """清空已记录事件。"""
        self.events.clear()
