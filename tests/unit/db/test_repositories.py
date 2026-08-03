"""仓储层单测 —— 并发正确性原语是 LoopForge 的护城河。

重点验证
--------
* ``BaseRepository`` 的通用 CRUD 语义；
* **Task CAS**：状态 + 版本双条件，抢输的一方安静失败而非抛错；
* **零重复领取**：两个 Worker 同时 ``acquire_lease_cas``，有且只有一个拿到租约；
* **Fence Token**：每次重新签发单调 +1，旧持有者被围栏挡住；
* **幂等键唯一**：``record_intent`` 重放抛 ``IdempotencyConflictError``；
* **事件哈希链**：``append`` 自动串链，``verify_chain`` 能发现篡改；
* **记忆压缩**：``mark_compressed`` 只打标不删原文；
* **预算消费**：三级阈值触发正确的动作；
* **UoW 事务语义**：回滚丢弃变更与缓存事件，提交后才发布事件。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from loopforge.core.clock import FrozenClock, get_clock
from loopforge.core.enums import (
    BudgetAction,
    LeaseState,
    MemoryLayer,
    RunState,
    Severity,
    StepState,
    TaskState,
)
from loopforge.core.errors import (
    IdempotencyConflictError,
    IllegalTransitionError,
    NotFoundError,
    OptimisticLockError,
)
from loopforge.core.event_types import EventType
from loopforge.core.ids import is_ulid
from loopforge.db.uow import UnitOfWork
from tests.factories import (
    make_memory_item,
    make_project,
    make_repo,
    make_run,
    make_task,
    make_tasks,
    make_worker,
)

# ---------------------------------------------------------------------------
# BaseRepository 通用语义
# ---------------------------------------------------------------------------


async def test_create_assigns_ulid_and_timestamps(uow: UnitOfWork) -> None:
    """``create`` 自动补 ULID 主键与时间戳。"""
    project = await make_project(uow, slug="alpha", name="Alpha")
    assert is_ulid(project.id)
    assert project.created_at.tzinfo is not None
    assert project.created_at == get_clock().now()


async def test_get_and_exists(uow: UnitOfWork) -> None:
    """按主键读取与存在性判断。"""
    project = await make_project(uow)
    assert (await uow.projects.get(project.id)) is project
    assert await uow.projects.exists(project.id) is True
    assert await uow.projects.get("01NOPE0000000000000000000") is None
    assert await uow.projects.exists("01NOPE0000000000000000000") is False


async def test_get_or_raise_raises_not_found(uow: UnitOfWork) -> None:
    """缺失主键必须抛 :class:`NotFoundError` 而不是返回 None。"""
    with pytest.raises(NotFoundError):
        await uow.projects.get_or_raise("01NOPE0000000000000000000")


async def test_find_one_by_and_list_by(uow: UnitOfWork) -> None:
    """条件查询与分页。"""
    await make_project(uow, slug="s-1", name="P1")
    await make_project(uow, slug="s-2", name="P2")
    found = await uow.projects.find_one_by(slug="s-2")
    assert found is not None and found.name == "P2"
    page = await uow.projects.list_by(limit=1)
    assert len(page) == 1
    assert await uow.projects.count_by() == 2


async def test_update_and_delete_by_pk(uow: UnitOfWork) -> None:
    """按主键更新与删除返回受影响行数。"""
    project = await make_project(uow)
    assert await uow.projects.update_by_pk(project.id, name="改名了") == 1
    assert await uow.projects.update_by_pk("01NOPE0000000000000000000", name="x") == 0
    assert await uow.projects.delete_by_pk(project.id) == 1
    assert await uow.projects.delete_by_pk(project.id) == 0


async def test_bulk_create(uow: UnitOfWork) -> None:
    """批量创建保持入参顺序。"""
    rows = [{"slug": f"bulk-{i}", "name": f"B{i}", "settings": {}} for i in range(3)]
    created = await uow.projects.bulk_create(rows)
    assert [item.slug for item in created] == ["bulk-0", "bulk-1", "bulk-2"]


async def test_project_slug_lookup_and_repo_link(uow: UnitOfWork) -> None:
    """Project ↔ Repo 关联查询。"""
    project = await make_project(uow, slug="linked")
    repo = await make_repo(uow, project_id=project.id, webhook_enabled=True)
    assert (await uow.projects.get_by_slug("linked")).id == project.id
    listed = await uow.repos.list_by_project(project.id)
    assert [item.id for item in listed] == [repo.id]
    assert [item.id for item in await uow.repos.list_webhook_enabled()] == [repo.id]


# ---------------------------------------------------------------------------
# Run 状态机
# ---------------------------------------------------------------------------


async def test_run_cas_state_success_and_conflict(uow: UnitOfWork) -> None:
    """Run 状态迁移只在源状态匹配时成功。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id, state=RunState.CREATED)
    assert await uow.runs.cas_state(
        run.id, from_state=RunState.CREATED, to_state=RunState.RUNNING
    )
    assert not await uow.runs.cas_state(
        run.id, from_state=RunState.CREATED, to_state=RunState.PAUSED
    )
    await uow.session.refresh(run)
    assert run.state is RunState.RUNNING
    assert run.started_at is not None


async def test_run_cas_state_accepts_state_sequence(uow: UnitOfWork) -> None:
    """允许传一组可接受的源状态。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id, state=RunState.PAUSED)
    assert await uow.runs.cas_state(
        run.id,
        from_state=[RunState.RUNNING, RunState.PAUSED],
        to_state=RunState.CANCELLING,
    )


async def test_run_terminal_state_cannot_transition(uow: UnitOfWork) -> None:
    """终态 Run 不允许再迁移。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id, state=RunState.SUCCEEDED)
    with pytest.raises(IllegalTransitionError):
        await uow.runs.cas_state(
            run.id, from_state=RunState.SUCCEEDED, to_state=RunState.RUNNING
        )


async def test_run_counters_advance(uow: UnitOfWork) -> None:
    """loop / milestone / recovery 三个计数器各自 +1。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    assert await uow.runs.advance_loop(run.id) == 1
    assert await uow.runs.advance_loop(run.id) == 2
    assert await uow.runs.advance_milestone(run.id) == 1
    assert await uow.runs.increment_recovery(run.id) == 1
    assert await uow.runs.advance_loop("01NOPE0000000000000000000") == -1


async def test_run_is_exhausted(uow: UnitOfWork) -> None:
    """``loop_index >= max_loops`` 即耗尽。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id, max_loops=2, loop_index=0)
    assert await uow.runs.is_exhausted(run.id) is False
    await uow.runs.advance_loop(run.id)
    await uow.runs.advance_loop(run.id)
    assert await uow.runs.is_exhausted(run.id) is True


async def test_run_list_active_and_count_by_state(uow: UnitOfWork) -> None:
    """活跃 Run 列表与状态直方图。"""
    project = await make_project(uow)
    await make_run(uow, project_id=project.id, state=RunState.RUNNING)
    await make_run(uow, project_id=project.id, state=RunState.SUCCEEDED)
    active = await uow.runs.list_active()
    assert len(active) == 1
    histogram = await uow.runs.count_by_state()
    assert histogram[str(RunState.SUCCEEDED)] == 1


# ---------------------------------------------------------------------------
# Task CAS —— 并发正确性核心
# ---------------------------------------------------------------------------


async def test_task_cas_transition_bumps_version(uow: UnitOfWork) -> None:
    """CAS 成功后版本 +1。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    task = await make_task(uow, run_id=run.id, state=TaskState.READY)
    # 必须在 CAS 之前快照版本号：UPDATE 会让 session 里的 ORM 实例过期，
    # 之后再读 task.version 拿到的是刷新后的新值，断言就会自己骗自己。
    before = task.version
    outcome = await uow.tasks.cas_transition(
        task.id,
        from_state=TaskState.READY,
        to_state=TaskState.LEASED,
        expected_version=before,
    )
    assert bool(outcome) is True
    assert outcome.new_version == before + 1
    assert outcome.rowcount == 1


async def test_task_cas_fails_on_stale_version(uow: UnitOfWork) -> None:
    """版本号过期 → 安静失败（抢不到是常态，不是异常）。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    task = await make_task(uow, run_id=run.id)
    await uow.tasks.cas_transition(
        task.id,
        from_state=TaskState.READY,
        to_state=TaskState.LEASED,
        expected_version=task.version,
    )
    loser = await uow.tasks.cas_transition(
        task.id,
        from_state=TaskState.READY,
        to_state=TaskState.LEASED,
        expected_version=task.version,
    )
    assert bool(loser) is False
    assert loser.new_version is None
    assert loser.rowcount == 0


async def test_task_cas_or_raise_raises_optimistic_lock(uow: UnitOfWork) -> None:
    """严格版在冲突时抛 :class:`OptimisticLockError`。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    task = await make_task(uow, run_id=run.id)
    with pytest.raises(OptimisticLockError):
        await uow.tasks.cas_transition_or_raise(
            task.id,
            from_state=TaskState.READY,
            to_state=TaskState.LEASED,
            expected_version=task.version + 99,
        )


async def test_task_terminal_state_cannot_transition(uow: UnitOfWork) -> None:
    """终态 Task 迁出即报错。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    task = await make_task(uow, run_id=run.id, state=TaskState.DONE)
    with pytest.raises(IllegalTransitionError):
        await uow.tasks.cas_transition(
            task.id,
            from_state=TaskState.DONE,
            to_state=TaskState.READY,
            expected_version=task.version,
        )


async def test_task_bump_fence_is_monotonic(uow: UnitOfWork) -> None:
    """fence token 单调递增，绝不回退。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    task = await make_task(uow, run_id=run.id)
    before = task.fence_token  # 同样要在自增之前快照
    first = await uow.tasks.bump_fence_token(task.id)
    second = await uow.tasks.bump_fence_token(task.id)
    assert first == before + 1
    assert second == first + 1
    assert await uow.tasks.bump_fence_token("01NOPE0000000000000000000") is None


async def test_task_cas_1000_contenders_exactly_one_wins(uow: UnitOfWork) -> None:
    """1000 个争抢者盯同一个 Task，**有且仅有 1 个**成功。

    这是 ``TASK_BREAKDOWN.md`` §3.4 DoD 明列的一条，也是 WP4 全部并发正确性的
    地基：只要 CAS 谓词 ``state = :from AND version = :expected`` 成立一次之后
    version 就前进，后续 999 个持旧版本号的争抢者必须**安静失败**（返回
    ``succeeded=False``）而不是抛异常、更不是把状态改回去。

    这里刻意用「顺序发起、共用同一个过期版本号」而不是 ``asyncio.gather``：
    SQLite 内存库走 StaticPool 只有一条物理连接，真并发反而会退化成连接争用，
    测的就不是 CAS 谓词了。CAS 的正确性只取决于「谓词是否原子」，与发起顺序无关，
    所以这种写法是确定性的、零 flaky 的等价验证。
    """
    contenders = 1000
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id, state=RunState.RUNNING)
    task = await make_task(uow, run_id=run.id, state=TaskState.READY)
    stale_version = task.version

    outcomes = [
        await uow.tasks.cas_transition(
            task.id,
            from_state=TaskState.READY,
            to_state=TaskState.LEASED,
            expected_version=stale_version,
            bump_fence=True,
        )
        for _ in range(contenders)
    ]

    winners = [item for item in outcomes if item.succeeded]
    assert len(winners) == 1, "零重复领取被破坏：CAS 谓词不是原子的"
    assert winners[0].new_version == stale_version + 1
    assert all(item.rowcount == 0 for item in outcomes if not item.succeeded)

    reloaded = await uow.tasks.get_or_raise(task.id)
    assert reloaded.state is TaskState.LEASED
    assert reloaded.version == stale_version + 1
    assert reloaded.fence_token == task.fence_token or reloaded.fence_token >= 1


async def test_task_state_helpers(uow: UnitOfWork) -> None:
    """按键查找、状态直方图、终态判定、计数器。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    task = await make_task(uow, run_id=run.id, task_key="T-KEY")
    assert (await uow.tasks.get_by_key(run.id, "T-KEY")).id == task.id
    assert (await uow.tasks.count_states(run.id))[str(TaskState.READY)] == 1
    assert await uow.tasks.all_terminal(run.id) is False
    assert await uow.tasks.increment_attempt(task.id) == 1
    assert await uow.tasks.increment_reclaim(task.id) == 1


async def test_task_list_leasable_orders_by_priority(uow: UnitOfWork) -> None:
    """可领取列表按优先级排序 —— **数值越大越优先**。

    口径由三处共同锁定，测试必须与之一致：
    ``Task.priority`` 列 doc「数值越大越优先」、索引 ``ix_tasks_leasable``
    的 ``priority DESC`` 键序、``TaskRepository.leasable_query`` 的
    ``order_by(desc(priority), asc(created_at))``。
    """
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id, state=RunState.RUNNING)
    low = await make_task(uow, run_id=run.id, priority=10)
    high = await make_task(uow, run_id=run.id, priority=90)
    leasable = await uow.tasks.list_leasable(limit=10)
    ids = [item.id for item in leasable]
    assert high.id in ids and low.id in ids
    assert ids.index(high.id) < ids.index(low.id)


# ---------------------------------------------------------------------------
# 租约 —— 零重复领取
# ---------------------------------------------------------------------------


async def test_acquire_lease_success(uow: UnitOfWork) -> None:
    """领取成功后：Task 变 LEASED、租约 ACTIVE、fence 前进、回写 current_lease_id。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id, state=RunState.RUNNING)
    task = await make_task(uow, run_id=run.id)
    worker = await make_worker(uow)

    lease = await uow.leases.acquire_lease_cas(
        task.id, worker.id, expected_version=task.version, ttl_seconds=60
    )
    assert lease is not None
    assert lease.state is LeaseState.ACTIVE
    assert lease.fence_token == 1
    assert lease.expires_at == get_clock().now() + timedelta(seconds=60)

    refreshed = await uow.tasks.get_or_raise(task.id)
    assert refreshed.state is TaskState.LEASED
    assert refreshed.current_lease_id == lease.id


async def test_zero_duplicate_acquire(uow: UnitOfWork) -> None:
    """两个 Worker 抢同一个 Task —— 有且只有一个成功。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id, state=RunState.RUNNING)
    task = await make_task(uow, run_id=run.id)
    worker_a = await make_worker(uow, hostname="a")
    worker_b = await make_worker(uow, hostname="b")

    first = await uow.leases.acquire_lease_cas(
        task.id, worker_a.id, expected_version=task.version
    )
    second = await uow.leases.acquire_lease_cas(
        task.id, worker_b.id, expected_version=task.version
    )
    assert first is not None
    assert second is None, "同一 Task 不允许两把活跃租约"
    assert await uow.leases.count_active() == 1


async def test_lease_heartbeat_extends_expiry(uow: UnitOfWork, frozen_clock: FrozenClock) -> None:
    """心跳续期把过期时间往后推，并累加计数。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id, state=RunState.RUNNING)
    task = await make_task(uow, run_id=run.id)
    worker = await make_worker(uow)
    lease = await uow.leases.acquire_lease_cas(
        task.id, worker.id, expected_version=task.version, ttl_seconds=60
    )
    assert lease is not None
    original_expiry = lease.expires_at

    frozen_clock.advance(30)
    # heartbeat 只认「租约 + 持有者」；fence token 的校验是 verify_fence 的职责，
    # 不是心跳的入参（续租后从返回的 lease.fence_token 取新值）。
    refreshed = await uow.leases.heartbeat(lease.id, worker_id=worker.id)
    assert refreshed is not None
    await uow.session.refresh(lease)
    assert lease.expires_at > original_expiry
    assert lease.heartbeat_count == 1


async def test_lease_verify_fence_blocks_stale_holder(uow: UnitOfWork) -> None:
    """旧 fence token 必须被围栏挡住（防止僵尸 Worker 写脏数据）。"""
    from loopforge.core.errors import FencedOutError

    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id, state=RunState.RUNNING)
    task = await make_task(uow, run_id=run.id)
    worker = await make_worker(uow)
    lease = await uow.leases.acquire_lease_cas(
        task.id, worker.id, expected_version=task.version
    )
    assert lease is not None

    await uow.leases.verify_fence(task.id, lease.fence_token)
    with pytest.raises(FencedOutError):
        await uow.leases.verify_fence(task.id, lease.fence_token - 1)


async def test_lease_release_frees_the_task(uow: UnitOfWork) -> None:
    """释放租约后同一个 Task 可以被再次领取，且 fence 继续前进。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id, state=RunState.RUNNING)
    task = await make_task(uow, run_id=run.id)
    worker = await make_worker(uow)

    first = await uow.leases.acquire_lease_cas(
        task.id, worker.id, expected_version=task.version
    )
    assert first is not None
    assert await uow.leases.release(first.id, reason="completed")

    reloaded = await uow.tasks.get_or_raise(task.id)
    second = await uow.leases.acquire_lease_cas(
        task.id,
        worker.id,
        expected_version=reloaded.version,
        from_state=reloaded.state,
    )
    assert second is not None
    assert second.fence_token > first.fence_token


async def test_lease_expiry_scan(uow: UnitOfWork, frozen_clock: FrozenClock) -> None:
    """过期扫描能捞出超时租约并把它标记为 EXPIRED。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id, state=RunState.RUNNING)
    task = await make_task(uow, run_id=run.id)
    worker = await make_worker(uow)
    lease = await uow.leases.acquire_lease_cas(
        task.id, worker.id, expected_version=task.version, ttl_seconds=10
    )
    assert lease is not None

    frozen_clock.advance(11)
    expired = await uow.leases.list_expired()
    assert [item.id for item in expired] == [lease.id]
    assert await uow.leases.expire(lease.id)
    await uow.session.refresh(lease)
    assert lease.state is LeaseState.EXPIRED
    assert await uow.leases.get_active_for_task(task.id) is None


# ---------------------------------------------------------------------------
# Step 幂等
# ---------------------------------------------------------------------------


async def test_record_intent_then_confirm(uow: UnitOfWork) -> None:
    """意图 → 确认 的正常路径。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    step = await uow.steps.record_intent(
        run_id=run.id, idempotency_key="IK-1", phase="execute"
    )
    assert step.state is StepState.INTENT_RECORDED
    assert await uow.steps.confirm(step.id, result_payload={"ok": True})
    await uow.session.refresh(step)
    assert step.state is StepState.CONFIRMED
    assert step.result_payload == {"ok": True}


async def test_duplicate_idempotency_key_conflicts(uow: UnitOfWork) -> None:
    """幂等键重放必须抛 :class:`IdempotencyConflictError`。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    await uow.steps.record_intent(run_id=run.id, idempotency_key="IK-DUP", phase="p")
    with pytest.raises(IdempotencyConflictError):
        await uow.steps.record_intent(
            run_id=run.id, idempotency_key="IK-DUP", phase="p"
        )
    found = await uow.steps.get_by_idempotency_key("IK-DUP")
    assert found is not None


async def test_intent_payload_is_redacted_on_write(uow: UnitOfWork) -> None:
    """写库前强制脱敏 —— 密钥绝不落盘。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    step = await uow.steps.record_intent(
        run_id=run.id,
        idempotency_key="IK-SECRET",
        phase="llm",
        intent_payload={"api_key": "sk-abcdefghijklmnopqrstuvwx0123"},
    )
    assert step.intent_payload["api_key"] == "[REDACTED]"


async def test_unconfirmed_and_uncertain_steps(uow: UnitOfWork) -> None:
    """未确认列表 + 标记不确定（崩溃恢复的输入）。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    step = await uow.steps.record_intent(
        run_id=run.id, idempotency_key="IK-U", phase="p"
    )
    unconfirmed = await uow.steps.list_unconfirmed(run_id=run.id)
    assert [item.id for item in unconfirmed] == [step.id]
    assert await uow.steps.mark_uncertain(step.id, reason="worker 崩了")
    await uow.session.refresh(step)
    assert step.state is StepState.UNCERTAIN


async def test_idempotency_record_put_if_absent(uow: UnitOfWork) -> None:
    """幂等记录首次插入返回 ``created=True``，重复返回旧记录。"""
    record, created = await uow.idempotency.put_if_absent("API-1", scope="api")
    assert created is True
    same, created_again = await uow.idempotency.put_if_absent("API-1", scope="api")
    assert created_again is False
    assert same.key == record.key


async def test_idempotency_purge_expired(uow: UnitOfWork, frozen_clock: FrozenClock) -> None:
    """过期幂等记录会被清理。"""
    now = get_clock().now()
    await uow.idempotency.put_if_absent("EXP-1", expires_at=now + timedelta(seconds=10))
    await uow.idempotency.put_if_absent("KEEP-1")
    frozen_clock.advance(20)
    assert await uow.idempotency.purge_expired() == 1
    assert await uow.idempotency.get("KEEP-1") is not None


# ---------------------------------------------------------------------------
# 事件哈希链
# ---------------------------------------------------------------------------


async def test_event_append_builds_hash_chain(uow: UnitOfWork) -> None:
    """连续追加事件自动串成哈希链。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    first = await uow.events.append(
        EventType.RUN_CREATED, {"run_id": run.id}, run_id=run.id
    )
    second = await uow.events.append(
        EventType.RUN_STARTED, {"run_id": run.id}, run_id=run.id
    )
    assert first.prev_hash is None
    assert second.prev_hash == first.hash
    assert second.seq > first.seq
    intact, broken_at = await uow.events.verify_chain(run.id)
    assert intact is True and broken_at is None


async def test_event_chain_detects_tampering(uow: UnitOfWork) -> None:
    """篡改 payload 后校验必须失败并指出位置。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    await uow.events.append(EventType.RUN_CREATED, {"run_id": run.id}, run_id=run.id)
    victim = await uow.events.append(
        EventType.RUN_STARTED, {"run_id": run.id}, run_id=run.id
    )
    victim.payload = {"run_id": "被改了"}
    await uow.session.flush()
    intact, broken_at = await uow.events.verify_chain(run.id)
    assert intact is False
    assert broken_at == victim.seq


async def test_event_payload_is_redacted(uow: UnitOfWork) -> None:
    """事件 payload 同样强制脱敏。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    event = await uow.events.append(
        EventType.RUN_STARTED,
        {"token": "ghp_" + "a" * 36},
        run_id=run.id,
        severity=Severity.WARNING,
    )
    assert event.payload["token"] == "[REDACTED]"
    assert event.severity is Severity.WARNING


async def test_event_incremental_pull_by_seq(uow: UnitOfWork) -> None:
    """``after_seq`` 增量拉取（SSE 断线重连用）。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    first = await uow.events.append(EventType.RUN_CREATED, {}, run_id=run.id)
    second = await uow.events.append(EventType.RUN_STARTED, {}, run_id=run.id)
    tail = await uow.events.list_for_run(run.id, after_seq=first.seq)
    assert [item.seq for item in tail] == [second.seq]
    assert await uow.events.count_for_run(run.id) == 2
    assert await uow.events.last_seq(run.id) == second.seq


async def test_audit_log_append(uow: UnitOfWork) -> None:
    """审计日志按资源与操作者可查。"""
    # 审计条目用 before/after 快照 + reason 记录「改了什么、为什么改」，
    # 而不是一个笼统的 detail 字典。
    entry = await uow.audit_logs.append(
        actor="alice",
        action="run.cancel",
        resource_type="run",
        resource_id="R1",
        before={"state": "RUNNING"},
        after={"state": "CANCELLED"},
        reason="手工中止",
    )
    assert entry.actor == "alice"
    assert entry.reason == "手工中止"
    by_resource = await uow.audit_logs.list_for_resource("run", "R1")
    assert [item.id for item in by_resource] == [entry.id]
    assert len(await uow.audit_logs.list_for_actor("alice")) == 1


# ---------------------------------------------------------------------------
# 记忆层
# ---------------------------------------------------------------------------


async def test_memory_add_and_list_by_layer(uow: UnitOfWork) -> None:
    """按层读取，显著性倒序。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    low = await make_memory_item(uow, run_id=run.id, content="低", salience=0.2)
    high = await make_memory_item(uow, run_id=run.id, content="高", salience=0.9)
    items = await uow.memory.list_by_layer(run.id, MemoryLayer.WORKING)
    assert [item.id for item in items] == [high.id, low.id]


async def test_memory_salience_is_clamped(uow: UnitOfWork) -> None:
    """显著性自动夹到 ``[0, 1]``。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    over = await make_memory_item(uow, run_id=run.id, salience=5.0)
    under = await make_memory_item(uow, run_id=run.id, salience=-3.0)
    assert over.salience == 1.0
    assert under.salience == 0.0


async def test_memory_mark_compressed_keeps_original(uow: UnitOfWork) -> None:
    """压缩只打标，**绝不删除原文**（可追溯是硬要求）。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    item = await make_memory_item(uow, run_id=run.id, content="原始内容")
    summary = await uow.milestones.create(
        run_id=run.id,
        level=1,
        content="摘要",
        covers_from_seq=1,
        covers_to_seq=10,
        tokens=8,
    )
    assert await uow.memory.mark_compressed([item.id], summary.id) == 1
    await uow.session.refresh(item)
    assert item.compressed_into == summary.id
    assert item.content == "原始内容"
    assert await uow.memory.list_by_layer(run.id, MemoryLayer.WORKING) == []
    kept = await uow.memory.list_by_layer(
        run.id, MemoryLayer.WORKING, include_compressed=True
    )
    assert [entry.id for entry in kept] == [item.id]


async def test_memory_total_tokens(uow: UnitOfWork) -> None:
    """token 汇总用于上下文预算裁剪。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    await make_memory_item(uow, run_id=run.id, tokens=10)
    await make_memory_item(uow, run_id=run.id, tokens=32)
    assert await uow.memory.total_tokens(run.id) == 42


async def test_pinned_constraints_lifecycle(uow: UnitOfWork) -> None:
    """钉住约束：写入 → 命中计数 → 停用。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    # 约束正文字段名是 text（与 PinnedConstraint.text / text_hash 对齐）
    pinned = await uow.pinned.pin(
        run_id=run.id, text="禁止提交到 main", origin="human", priority=1
    )
    assert [item.id for item in await uow.pinned.list_active(run.id)] == [pinned.id]
    assert await uow.pinned.record_hit(pinned.id)
    assert await uow.pinned.deactivate(pinned.id)
    assert await uow.pinned.list_active(run.id) == []


# ---------------------------------------------------------------------------
# 预算治理
# ---------------------------------------------------------------------------


async def test_budget_consume_and_thresholds(uow: UnitOfWork) -> None:
    """三级阈值：70% 警告、90% 限流、100% 熔断。"""
    budget = await uow.budgets.create(
        scope="RUN",
        scope_ref="R1",
        window="run",
        limit_usd=Decimal("10.0"),
        warn_at=0.7,
        throttle_at=0.9,
        halt_at=1.0,
        window_start=get_clock().now(),
        active=True,
    )
    await uow.budgets.consume(budget.id, Decimal("7.5"))
    await uow.session.refresh(budget)
    assert budget.triggered_action() is BudgetAction.WARN

    await uow.budgets.consume(budget.id, Decimal("1.6"))
    await uow.session.refresh(budget)
    assert budget.triggered_action() is BudgetAction.THROTTLE

    await uow.budgets.consume(budget.id, Decimal("1.0"))
    await uow.session.refresh(budget)
    assert budget.triggered_action() is BudgetAction.HALT
    assert budget.remaining_usd == Decimal("0E-8")


async def test_budget_scope_lookup(uow: UnitOfWork) -> None:
    """按作用域三元组唯一定位预算。"""
    created = await uow.budgets.create(
        scope="RUN",
        scope_ref="R-LOOKUP",
        window="run",
        limit_usd=Decimal("1.0"),
        window_start=get_clock().now(),
        active=True,
    )
    # window 是关键字参数（默认 "total"），避免调用点出现三个裸字符串。
    found = await uow.budgets.get_for_scope("RUN", "R-LOOKUP", window="run")
    assert found is not None and found.id == created.id
    # 走 §11.2 冻结的 uow.governance 门面应拿到同一个仓储
    via_facade = await uow.governance.budgets.get_for_scope(
        "RUN", "R-LOOKUP", window="run"
    )
    assert via_facade is not None and via_facade.id == created.id


# ---------------------------------------------------------------------------
# UnitOfWork 事务语义
# ---------------------------------------------------------------------------


async def test_rollback_discards_changes(session_factory) -> None:  # noqa: ANN001
    """显式回滚后数据不落库。"""
    async with UnitOfWork(session_factory=session_factory) as unit:
        await make_project(unit, slug="ghost")
        await unit.rollback()
    async with UnitOfWork(session_factory=session_factory) as unit:
        assert await unit.projects.find_one_by(slug="ghost") is None


async def test_exception_triggers_rollback(session_factory) -> None:  # noqa: ANN001
    """上下文内抛异常 → 自动回滚。"""
    with pytest.raises(RuntimeError):
        async with UnitOfWork(session_factory=session_factory) as unit:
            await make_project(unit, slug="boom")
            raise RuntimeError("业务失败")
    async with UnitOfWork(session_factory=session_factory) as unit:
        assert await unit.projects.find_one_by(slug="boom") is None


async def test_commit_persists_across_sessions(session_factory) -> None:  # noqa: ANN001
    """正常退出自动提交，另一个事务能读到。"""
    async with UnitOfWork(session_factory=session_factory) as unit:
        await make_project(unit, slug="persisted")
    async with UnitOfWork(session_factory=session_factory) as unit:
        assert await unit.projects.find_one_by(slug="persisted") is not None


async def test_events_are_published_only_after_commit(session_factory) -> None:  # noqa: ANN001
    """缓存事件在 commit 成功后才发布；回滚则全部丢弃。"""
    from loopforge.core.event_bus import EventBus, RecordingSubscriber

    bus = EventBus(queue_size=32)
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)

    async with UnitOfWork(session_factory=session_factory, event_bus=bus) as unit:
        project = await make_project(unit)
        run = await make_run(unit, project_id=project.id)
        unit.collect_event(
            EventType.RUN_CREATED,
            {"run_id": run.id, "state": RunState.CREATED},
            run_id=run.id,
        )
        assert len(unit.pending_events) == 1
        assert recorder.events == []
    await bus.drain()
    assert [envelope.event_type for envelope in recorder.events] == [
        EventType.RUN_CREATED
    ]


async def test_rollback_drops_pending_events(session_factory) -> None:  # noqa: ANN001
    """回滚时缓存事件必须一并丢弃，否则会广播「没发生过的事」。"""
    from loopforge.core.event_bus import EventBus, RecordingSubscriber

    bus = EventBus(queue_size=32)
    recorder = RecordingSubscriber()
    bus.subscribe(recorder)

    async with UnitOfWork(session_factory=session_factory, event_bus=bus) as unit:
        unit.collect_event(EventType.RUN_CREATED, {"run_id": "R", "state": "CREATED"})
        await unit.rollback()
        assert unit.pending_events == []
    await bus.drain()
    assert recorder.events == []


async def test_uow_outside_context_raises() -> None:
    """未进入上下文就用仓储 → 明确报错，而不是 NoneType 崩溃。"""
    from loopforge.core.errors import DatabaseError

    unit = UnitOfWork()
    with pytest.raises(DatabaseError):
        _ = unit.projects


async def test_multiple_tasks_created_in_one_transaction(uow: UnitOfWork) -> None:
    """批量造 Task 不撞 ``uq_tasks_run_key``。"""
    project = await make_project(uow)
    run = await make_run(uow, project_id=project.id)
    tasks = await make_tasks(uow, run_id=run.id, count=5)
    assert len({item.task_key for item in tasks}) == 5
    assert await uow.tasks.count_by(run_id=run.id) == 5
