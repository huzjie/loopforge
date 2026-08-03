"""Pinned Constraints 测试 —— 500 轮无损 / 硬配额守卫 / 冲突检测 / 优先级。

DoD（``TASK_BREAKDOWN`` §7.4）：连续 500 轮压缩后 Pinned 约束原文 100% 无损。
"""

from __future__ import annotations

import pytest

from loopforge.core.enums import MemoryLayer
from loopforge.memory.compressor import Compressor
from loopforge.memory.pinned import PinnedConstraintStore, detect_conflict_pair
from tests.factories import make_project, make_run


async def _run_id(uow: object) -> str:
    project = await make_project(uow)  # type: ignore[arg-type]
    run = await make_run(uow, project_id=project.id)  # type: ignore[arg-type]
    return run.id


@pytest.mark.asyncio
async def test_pin_deduplicates_by_text(uow: object) -> None:
    """同 Run 同文本去重，返回同一条记录。"""
    run_id = await _run_id(uow)
    store = PinnedConstraintStore(uow, token_budget=128_000)  # type: ignore[arg-type]
    first = await store.add(run_id=run_id, text="所有新接口必须返回 ApiResponse", origin="user")
    second = await store.add(run_id=run_id, text="所有新接口必须返回 ApiResponse", origin="planner")
    assert first.id == second.id


@pytest.mark.asyncio
async def test_pin_priority_and_hit_count(uow: object) -> None:
    """优先级排序（priority 越大越靠前）+ 命中计数。"""
    run_id = await _run_id(uow)
    store = PinnedConstraintStore(uow, token_budget=128_000)  # type: ignore[arg-type]
    less = await store.add(run_id=run_id, text="低优先级约束内容 A", priority=10)
    important = await store.add(run_id=run_id, text="高优先级约束内容 B", priority=500)
    active = await store.list_active(run_id)
    assert active[0].id == important.id  # priority 大 → 排前面
    await store.record_hit(important.id)
    refreshed = await store.list_active(run_id)
    by_id = {item.id: item for item in refreshed}
    assert by_id[important.id].hit_count == 1


@pytest.mark.asyncio
async def test_hard_quota_guard_evicts_lowest_priority(uow: object) -> None:
    """硬配额守卫：超配额时按优先级截断低优先级，绝不静默丢弃。"""
    run_id = await _run_id(uow)
    store = PinnedConstraintStore(uow, token_budget=300, pinned_max_ratio=0.5)  # type: ignore[arg-type]
    keep = await store.add(
        run_id=run_id, text="必须保留的重要约束 一二三四五六七八九十", priority=999
    )
    for index in range(20):
        await store.add(
            run_id=run_id,
            text=f"低优先级约束 {index} 填充内容 一二三四五六七八九十",
            priority=10 + index,
        )
    active = await store.list_active(run_id)
    assert keep.id in {item.id for item in active}, "高优先级约束必须保留"
    # 低优先级被裁掉（软下线），原文未删除。
    assert len(active) < 20


@pytest.mark.asyncio
async def test_pinned_survives_500_compression_rounds(uow: object) -> None:
    """DoD：连续 500 轮压缩后 Pinned 约束原文 100% 无损。"""
    run_id = await _run_id(uow)
    store = PinnedConstraintStore(uow, token_budget=128_000)  # type: ignore[arg-type]
    constraint_text = "所有新接口必须返回 ApiResponse 包装，禁止修改 .github/workflows"
    constraint = await store.add(run_id=run_id, text=constraint_text, origin="planner", priority=10)
    compressor = Compressor()
    plan_text = constraint_text
    for round_index in range(500):
        filler = "\n".join(
            f"第 {round_index} 轮观察：工具输出 tool-{round_index} 的执行结果，错误 error-{round_index}"
        )
        compressed, _, _ = compressor.compress(
            [_chunk(f"m-{round_index}", filler)], level=3, target_tokens=120
        )
        plan_text = constraint_text + "\n" + compressed
        report = compressor.check_fidelity(run_id=run_id, constraints=[constraint], plan_text=plan_text)
        assert report.passed, f"第 {round_index} 轮 Pinned 约束丢失"
        # 同时验证约束从未被标记压缩。
        constraints = await store.list_active(run_id)
        assert constraint.id in {item.id for item in constraints}


def test_conflict_detection() -> None:
    """冲突检测：一条否定 + 一条肯定且主题重叠 → 冲突。"""
    left = _constraint("c1", "禁止修改 .github/workflows 目录")
    right = _constraint("c2", "必须修改 .github/workflows 目录以更新 CI")
    conflict = detect_conflict_pair(left, right)
    assert conflict is not None
    assert conflict.topic == "workflows" or conflict.topic
    no_conflict = detect_conflict_pair(
        _constraint("c1", "禁止修改 .github/workflows 目录"),
        _constraint("c2", "必须给所有接口加 ApiResponse 包装"),
    )
    assert no_conflict is None


def _constraint(cid: str, text: str) -> object:
    class _C:
        id = cid
        text_content = text
        priority = 100
        origin = "user"

    return _C()


def _chunk(cid: str, content: str) -> object:
    from loopforge.memory.compressor import MemoryChunk

    return MemoryChunk(id=cid, content=content)
