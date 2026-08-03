"""审计哈希链测试 —— 链完整性 / 篡改精确定位 / 导出。

DoD（``TASK_BREAKDOWN`` §7.4）：1 万条事件链完整；人为篡改中间一条
能被精确定位。
"""

from __future__ import annotations

import pytest

from loopforge.governance.audit.chain import AuditChain
from loopforge.governance.audit.exporter import AuditExporter
from loopforge.core.hashing import chain_hash

pytestmark = pytest.mark.asyncio


async def test_chain_append_and_verify_memory() -> None:
    """内存链：append 返回哈希，verify 完好。"""
    chain = AuditChain()  # 纯内存模式
    h1 = await chain.append(payload={"action": "run_started", "run_id": "r1"})
    h2 = await chain.append(payload={"action": "tool_called", "tool": "write_file"})
    assert len(h1) == 64 and len(h2) == 64
    assert h1 != h2
    report = await chain.verify()
    assert report.ok is True
    assert report.total == 2
    assert report.verified == 2


async def test_chain_tamper_detected_memory() -> None:
    """内存链：篡改中间一条 → verify 精确定位到该条。"""
    chain = AuditChain()
    await chain.append(payload={"seq_marker": 0, "msg": "first"})
    await chain.append(payload={"seq_marker": 1, "msg": "second"})
    await chain.append(payload={"seq_marker": 2, "msg": "third"})
    # 篡改第二条。
    chain._memory["__global__"][1].payload["msg"] = "TAMPERED"
    report = await chain.verify()
    assert report.ok is False
    assert report.broken_at == 1
    assert report.broken_expected_hash != report.broken_actual_hash


async def test_chain_hash_links_correctly() -> None:
    """哈希链公式：hash_n = chain_hash(prev_hash, payload)。"""
    chain = AuditChain()
    h1 = await chain.append(payload={"n": 1})
    h2 = await chain.append(payload={"n": 2})
    expected_h2 = chain_hash(h1, {"n": 2})
    assert h2 == expected_h2


async def test_chain_10000_records_integrity() -> None:
    """DoD：1 万条事件链完整。"""
    chain = AuditChain()
    for index in range(10_000):
        await chain.append(payload={"index": index, "action": f"event_{index}"})
    report = await chain.verify()
    assert report.ok is True
    assert report.total == 10_000


async def test_db_backed_chain_and_tamper(session_factory: object) -> None:
    """DB 后端：持久化链 + 篡改精确定位。"""
    chain = AuditChain(session_factory=session_factory)
    await chain.append(payload={"n": 0}, run_id="r1")
    await chain.append(payload={"n": 1}, run_id="r1")
    await chain.append(payload={"n": 2}, run_id="r1")
    assert (await chain.verify(run_id="r1")).ok is True
    # 篡改第二条（改 audit_logs 的 after 里的 payload）。
    from loopforge.db.uow import UnitOfWork

    async with UnitOfWork(session_factory=session_factory) as uow:  # type: ignore[arg-type]
        rows = await uow.audit_logs.list_for_resource("audit_chain", "r1")
        target = [row for row in rows if row.after["chain"]["seq"] == 1][0]
        # 重新赋值触发变更跟踪（JsonType 非 MutableDict，原地改不落库）。
        chain_after = dict(target.after)
        chain_after["chain"] = {**chain_after["chain"], "payload": {"n": 999}}
        target.after = chain_after
    report = await chain.verify(run_id="r1")
    assert report.ok is False
    assert report.broken_at == 1


async def test_exporter_formats() -> None:
    """JSON / JSONL / CSV 导出。"""
    chain = AuditChain()
    await chain.append(payload={"a": 1})
    await chain.append(payload={"a": 2})
    records = chain._memory["__global__"]
    exporter = AuditExporter("json")
    json_out = exporter.export(records)
    assert '"seq"' in json_out
    jsonl = AuditExporter("jsonl").export(records)
    assert jsonl.count("\n") == 1
    csv_out = AuditExporter("csv").export(records)
    assert csv_out.startswith("seq,prev_hash,hash,payload,created_at")
