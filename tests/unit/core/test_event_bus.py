"""``loopforge.core.event_bus`` 与 ``loopforge.core.event_types`` 单测。

事件总线是 WP4/WP6/WP7 的公共命脉，本测试锁死以下契约：

* 72 个事件类型 + payload 映射齐全（冻结公共词汇）；
* ``publish`` 严格校验 payload，类型不匹配立刻抛错；
* ``seq`` 从 1 开始单调递增，并发发布不重号；
* 订阅过滤、取消订阅幂等；
* 落库钩子在入队**之前**执行（先落库再扇出）；
* 订阅者抛异常不拖垮总线；
* 背压三策略（drop_oldest / drop_newest / block）行为正确；
* ``drain`` / ``stop`` 的排空语义。
"""

from __future__ import annotations

import asyncio
import re

import pytest
from pydantic import ValidationError as PydanticValidationError

from loopforge.core.enums import RunState, Severity, TaskState
from loopforge.core.event_bus import (
    DEFAULT_QUEUE_SIZE,
    EventBus,
    EventBusStats,
    EventEnvelope,
    RecordingSubscriber,
    get_event_bus,
    set_event_bus,
)
from loopforge.core.event_types import (
    EVENT_GROUPS,
    EVENT_PAYLOAD_MAP,
    EVENT_TYPE_COUNT,
    EventType,
    RunLifecyclePayload,
    TaskLifecyclePayload,
    payload_model_for,
    validate_payload,
)


def _run_payload(run_id: str = "R1", state: RunState = RunState.RUNNING) -> RunLifecyclePayload:
    """构造一个合法的 Run 生命周期 payload。"""
    return RunLifecyclePayload(run_id=run_id, state=state)


# ---------------------------------------------------------------------------
# 冻结词汇
# ---------------------------------------------------------------------------


def test_event_type_count_is_frozen() -> None:
    """事件类型总数已冻结为 72，新增只能追加在分组段尾。"""
    assert EVENT_TYPE_COUNT == 72
    assert len(EventType) == 72
    assert len({member.value for member in EventType}) == 72


def test_every_event_type_has_payload_model() -> None:
    """每个事件都必须注册 payload 模型，否则 publish 会 KeyError。"""
    for event_type in EventType:
        model = payload_model_for(event_type)
        assert model is EVENT_PAYLOAD_MAP[event_type]


def test_event_groups_partition_all_types() -> None:
    """分组表必须无重复、无遗漏地覆盖全部事件类型。"""
    flattened = [item for group in EVENT_GROUPS.values() for item in group]
    assert len(flattened) == len(set(flattened)) == EVENT_TYPE_COUNT
    assert set(flattened) == set(EventType)


def test_event_type_values_are_upper_snake() -> None:
    """事件名统一「全大写下划线」格式，且成员名与字符串值一致。

    口径来自 ``docs/TASK_BREAKDOWN.md`` §12.2：全大写下划线、动词用过去式
    （``RUN_STARTED`` 而不是 ``RUN_START``），DB 与 API 直接落这个字符串。
    """
    for event_type in EventType:
        value = str(event_type.value)
        assert value == value.upper(), f"{value} 不是全大写"
        assert value == event_type.name, "成员名必须与字符串值逐字相同"
        assert " " not in value and "." not in value
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", value), f"{value} 不是 UPPER_SNAKE"


def test_payload_forbids_extra_fields() -> None:
    """payload 模型 ``extra="forbid"``，拼错字段名立刻暴露。"""
    with pytest.raises(PydanticValidationError):
        RunLifecyclePayload(run_id="R1", state=RunState.RUNNING, typo=1)


def test_payload_is_frozen() -> None:
    """payload 发布后不可变，防止下游改写。"""
    payload = _run_payload()
    with pytest.raises(PydanticValidationError):
        payload.run_id = "R2"


def test_validate_payload_accepts_mapping() -> None:
    """dict 形式的 payload（UoW 缓存事件用）也要能校验通过。"""
    validated = validate_payload(
        EventType.RUN_STARTED, {"run_id": "R1", "state": "RUNNING"}
    )
    assert isinstance(validated, RunLifecyclePayload)
    assert validated.state is RunState.RUNNING


def test_validate_payload_rejects_wrong_model() -> None:
    """把 Task payload 塞给 Run 事件必须失败。"""
    with pytest.raises(PydanticValidationError):
        validate_payload(
            EventType.RUN_STARTED,
            TaskLifecyclePayload(run_id="R1", task_id="T1", state=TaskState.READY),
        )


# ---------------------------------------------------------------------------
# 构造与属性
# ---------------------------------------------------------------------------


def test_default_queue_size() -> None:
    """默认队列容量与 ``orchestration.event_bus_queue_size`` 对齐。"""
    assert DEFAULT_QUEUE_SIZE == 10_000


def test_invalid_queue_size_rejected() -> None:
    """``queue_size`` 必须为正。"""
    with pytest.raises(ValueError, match="queue_size"):
        EventBus(queue_size=0)


def test_stats_snapshot_shape() -> None:
    """计数快照字段固定，供 Prometheus 导出。"""
    snapshot = EventBusStats().snapshot()
    assert set(snapshot) == {
        "published",
        "delivered",
        "dropped",
        "handler_errors",
        "persist_errors",
    }
    assert all(value == 0 for value in snapshot.values())


def test_global_bus_singleton_and_override() -> None:
    """``set_event_bus`` 覆盖单例，传 ``None`` 清空。"""
    custom = EventBus(queue_size=8)
    set_event_bus(custom)
    try:
        assert get_event_bus() is custom
    finally:
        set_event_bus(None)


# ---------------------------------------------------------------------------
# 发布与订阅
# ---------------------------------------------------------------------------


async def test_publish_returns_monotonic_seq() -> None:
    """``seq`` 从 1 开始单调递增。"""
    bus = EventBus(queue_size=16)
    first = await bus.publish(EventType.RUN_CREATED, _run_payload(state=RunState.CREATED))
    second = await bus.publish(EventType.RUN_STARTED, _run_payload())
    assert (first, second) == (1, 2)


async def test_concurrent_publish_has_no_duplicate_seq() -> None:
    """并发发布 100 条，seq 必须两两不同且连续。"""
    bus = EventBus(queue_size=256)
    seqs = await asyncio.gather(
        *(bus.publish(EventType.RUN_STARTED, _run_payload()) for _ in range(100))
    )
    assert sorted(seqs) == list(range(1, 101))


async def test_subscriber_receives_envelope() -> None:
    """订阅者拿到的是校验后的 :class:`EventEnvelope`。"""
    bus = EventBus(queue_size=16)
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)
    await bus.publish(
        EventType.RUN_STARTED, _run_payload(), run_id="R1", trace_id="TR1"
    )
    await bus.drain()
    assert len(recorder.events) == 1
    envelope = recorder.events[0]
    assert isinstance(envelope, EventEnvelope)
    assert envelope.event_type is EventType.RUN_STARTED
    assert envelope.run_id == "R1"
    assert envelope.trace_id == "TR1"
    assert envelope.severity is Severity.INFO
    assert isinstance(envelope.payload, RunLifecyclePayload)


async def test_subscription_type_filter() -> None:
    """带 ``types`` 的订阅只收关心的事件。"""
    bus = EventBus(queue_size=32)
    only_run = RecordingSubscriber()
    everything = RecordingSubscriber()
    bus.subscribe(only_run, types=[EventType.RUN_STARTED])
    bus.subscribe(everything)
    await bus.publish(EventType.RUN_STARTED, _run_payload())
    await bus.publish(
        EventType.TASK_READY,
        TaskLifecyclePayload(run_id="R1", task_id="T1", state=TaskState.READY),
    )
    await bus.drain()
    assert only_run.types() == [EventType.RUN_STARTED]
    assert len(everything.events) == 2


async def test_unsubscribe_stops_delivery_and_is_idempotent() -> None:
    """取消订阅后不再收事件；重复取消不报错。"""
    bus = EventBus(queue_size=16)
    recorder = RecordingSubscriber()
    subscription_id = bus.subscribe(recorder)
    assert bus.subscription_count == 1
    bus.unsubscribe(subscription_id)
    bus.unsubscribe(subscription_id)
    bus.unsubscribe("not-a-real-id")
    assert bus.subscription_count == 0
    await bus.publish(EventType.RUN_STARTED, _run_payload())
    await bus.drain()
    assert recorder.events == []


def test_subscribe_rejects_non_callable() -> None:
    """handler 必须可调用。"""
    bus = EventBus(queue_size=4)
    with pytest.raises(TypeError):
        bus.subscribe("not callable")  # type: ignore[arg-type]


async def test_publish_rejects_mismatched_payload() -> None:
    """payload 与事件类型不匹配 → 抛错且**不占用 seq 之外的副作用**。"""
    bus = EventBus(queue_size=8)
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)
    with pytest.raises(PydanticValidationError):
        await bus.publish(EventType.RUN_STARTED, TaskLifecyclePayload(
            run_id="R1", task_id="T1", state=TaskState.READY
        ))
    await bus.drain()
    assert recorder.events == []


async def test_recording_subscriber_clear() -> None:
    """``clear()`` 清空记录，便于分段断言。"""
    bus = EventBus(queue_size=8)
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)
    await bus.publish(EventType.RUN_STARTED, _run_payload())
    await bus.drain()
    assert recorder.events
    recorder.clear()
    assert recorder.events == []


# ---------------------------------------------------------------------------
# 落库钩子
# ---------------------------------------------------------------------------


async def test_persist_hook_runs_before_fanout() -> None:
    """钩子必须在扇出之前完成 —— 崩溃时不能出现「已投递未落库」。"""
    order: list[str] = []

    async def persist(envelope: EventEnvelope) -> None:
        order.append(f"persist:{envelope.seq}")

    async def handler(envelope: EventEnvelope) -> None:
        order.append(f"handle:{envelope.seq}")

    bus = EventBus(queue_size=8, persist_hook=persist)
    bus.subscribe(handler)
    await bus.publish(EventType.RUN_STARTED, _run_payload())
    await bus.drain()
    assert order == ["persist:1", "handle:1"]


async def test_persist_hook_failure_is_counted_not_raised() -> None:
    """落库失败只记数，不能拖垮业务主流程。"""

    async def broken(_: EventEnvelope) -> None:
        raise RuntimeError("磁盘满了")

    bus = EventBus(queue_size=8, persist_hook=broken)
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)
    await bus.publish(EventType.RUN_STARTED, _run_payload())
    await bus.drain()
    assert bus.stats.persist_errors == 1
    assert len(recorder.events) == 1


async def test_set_persist_hook_can_clear() -> None:
    """钩子可动态设置与清除。"""
    calls: list[int] = []

    async def persist(envelope: EventEnvelope) -> None:
        calls.append(envelope.seq)

    bus = EventBus(queue_size=8)
    bus.set_persist_hook(persist)
    await bus.publish(EventType.RUN_STARTED, _run_payload())
    bus.set_persist_hook(None)
    await bus.publish(EventType.RUN_STARTED, _run_payload())
    await bus.drain()
    assert calls == [1]


# ---------------------------------------------------------------------------
# 容错与背压
# ---------------------------------------------------------------------------


async def test_handler_error_is_isolated() -> None:
    """一个订阅者炸了，其余订阅者照常收事件。"""

    async def broken(_: EventEnvelope) -> None:
        raise ValueError("订阅者内部错误")

    bus = EventBus(queue_size=8)
    recorder = RecordingSubscriber()
    bus.subscribe(broken)
    bus.subscribe(recorder)
    await bus.publish(EventType.RUN_STARTED, _run_payload())
    await bus.drain()
    assert bus.stats.handler_errors == 1
    assert len(recorder.events) == 1


async def test_overflow_drop_oldest() -> None:
    """队列满时丢最老的，保证最新状态能到达。"""
    bus = EventBus(queue_size=2, overflow_policy="drop_oldest")
    for _ in range(5):
        await bus.publish(EventType.RUN_STARTED, _run_payload())
    assert bus.queue_size == 2
    assert bus.stats.dropped == 3
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)
    await bus.drain()
    assert [envelope.seq for envelope in recorder.events] == [4, 5]


async def test_overflow_drop_newest() -> None:
    """队列满时丢最新的，保留最早的现场。"""
    bus = EventBus(queue_size=2, overflow_policy="drop_newest")
    for _ in range(5):
        await bus.publish(EventType.RUN_STARTED, _run_payload())
    assert bus.queue_size == 2
    assert bus.stats.dropped == 3
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)
    await bus.drain()
    assert [envelope.seq for envelope in recorder.events] == [1, 2]


async def test_overflow_block_waits_for_consumer() -> None:
    """``block`` 策略下，消费者跟上后发布方即可继续。"""
    bus = EventBus(queue_size=2, overflow_policy="block")
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)
    await bus.start()
    try:
        for _ in range(6):
            await asyncio.wait_for(
                bus.publish(EventType.RUN_STARTED, _run_payload()), timeout=2.0
            )
        await bus.drain(timeout_s=2.0)
    finally:
        await bus.stop(drain=True, timeout_s=2.0)
    assert len(recorder.events) == 6
    assert bus.stats.dropped == 0


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


async def test_start_stop_lifecycle() -> None:
    """``start`` 后 running 为真，``stop`` 后为假且可重复调用。"""
    bus = EventBus(queue_size=8)
    assert bus.running is False
    await bus.start()
    assert bus.running is True
    await bus.stop(drain=True, timeout_s=2.0)
    assert bus.running is False
    await bus.stop(drain=False, timeout_s=1.0)
    assert bus.running is False


async def test_stop_drains_pending_events() -> None:
    """优雅停机要把积压事件投递完 —— 后台任务没起来时也不许丢。"""
    bus = EventBus(queue_size=32)
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)
    for _ in range(10):
        await bus.publish(EventType.RUN_STARTED, _run_payload())
    await bus.stop(drain=True, timeout_s=2.0)
    assert len(recorder.events) == 10


async def test_stop_drains_pending_events_when_started() -> None:
    """已启动的总线停机同样要排空积压。"""
    bus = EventBus(queue_size=32)
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)
    await bus.start()
    for _ in range(10):
        await bus.publish(EventType.RUN_STARTED, _run_payload())
    await bus.stop(drain=True, timeout_s=2.0)
    assert len(recorder.events) == 10
    assert bus.running is False


async def test_stop_without_drain_may_discard() -> None:
    """``drain=False`` 是显式的「不保证投递」快速停机路径。"""
    bus = EventBus(queue_size=32)
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)
    for _ in range(5):
        await bus.publish(EventType.RUN_STARTED, _run_payload())
    await bus.stop(drain=False, timeout_s=1.0)
    assert bus.running is False
    assert len(recorder.events) == 0


async def test_async_context_manager() -> None:
    """``async with EventBus()`` 自动 start / stop。"""
    recorder = RecordingSubscriber()
    async with EventBus(queue_size=8) as bus:
        bus.subscribe(recorder)
        await bus.publish(EventType.RUN_STARTED, _run_payload())
        await bus.drain(timeout_s=2.0)
        assert bus.running is True
    assert len(recorder.events) == 1


async def test_envelope_to_dict_is_json_ready() -> None:
    """``to_dict()`` 直接用于 WebSocket 推送与落库。"""
    import json

    bus = EventBus(queue_size=8)
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)
    await bus.publish(
        EventType.RUN_FAILED,
        # RunFailedPayload 没有 state 字段：事件类型本身已经表达了「失败」，
        # 再塞一个 state 属于冗余，且 payload 是 extra="forbid" 的。
        {
            "run_id": "R1",
            "error_code": "LF-RUN-0302",
            "message": "崩了",
        },
        run_id="R1",
        severity=Severity.ERROR,
    )
    await bus.drain()
    payload = recorder.events[0].to_dict()
    restored = json.loads(json.dumps(payload))
    assert restored["type"] == str(EventType.RUN_FAILED)
    assert restored["severity"] == str(Severity.ERROR)
    assert restored["payload"]["run_id"] == "R1"
    assert restored["created_at"].startswith("2026-08-03")


async def test_repr_contains_diagnostics() -> None:
    """``repr`` 要能一眼看出运行状态与积压量。"""
    bus = EventBus(queue_size=4)
    await bus.publish(EventType.RUN_STARTED, _run_payload())
    text = repr(bus)
    assert "EventBus" in text
    assert "queue" in text or "1" in text
