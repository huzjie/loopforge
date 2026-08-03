"""审计日志 —— ``AuditChain``（冻结契约 §11.7，哈希链）。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``audit/chain.py``、§7.3.2（G4）。

G4：**断链即告警，不得静默修复**。哈希公式
（``core.hashing.chain_hash``，全仓唯一实现）::

    hash_n = sha256( (prev_hash or GENESIS) + "|" + canonical_json(payload) )

实现方式：链记录持久化到 ``audit_logs`` 表（``resource_type="audit_chain"``，
``resource_id=run_id`` 或 ``__global__``），完整链记录放在 ``after["chain"]``
字典里 —— 不改表结构即可支撑可验证的哈希链。未传 session_factory 时退化为
纯内存链（单测用）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.clock import get_clock
from loopforge.core.enums import Severity
from loopforge.core.hashing import GENESIS_HASH, chain_hash
from loopforge.db.session import get_session_factory
from loopforge.db.uow import UnitOfWork

__all__ = ["AuditChain", "ChainVerifyReport", "ChainRecord"]


class ChainRecord(BaseModel):
    """一条审计链记录。"""

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=0, description="链内序号")
    prev_hash: str = Field(description="上一条哈希")
    hash: str = Field(description="本条哈希")
    payload: dict[str, Any] = Field(default_factory=dict, description="负载")
    created_at: str = Field(default="", description="创建时刻 ISO")


class ChainVerifyReport(BaseModel):
    """链完整性校验报告。"""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(default=0, ge=0, description="记录总数")
    verified: int = Field(default=0, ge=0, description="校验通过条数")
    ok: bool = Field(default=True, description="链是否完好")
    broken_at: int | None = Field(default=None, description="首个损坏位置 seq")
    broken_expected_hash: str | None = Field(default=None, description="期望哈希")
    broken_actual_hash: str | None = Field(default=None, description="实际哈希")
    message: str = Field(default="", description="说明")


class AuditChain:
    """不可篡改追加的审计哈希链。

    :param session_factory: 传入则持久化到 ``audit_logs`` 表；``None`` 纯内存。
    :param actor: 写入 audit_logs 的操作者标识。
    """

    def __init__(self, *, session_factory: Any | None = None, actor: str = "system") -> None:
        """初始化审计链。

        :param session_factory: 传入则持久化到 ``audit_logs`` 表；
            ``None`` 为纯内存模式（单测 / 轻量场景）。
        :param actor: 写入 audit_logs 的操作者标识。
        """
        self._session_factory: Any | None = session_factory
        self._actor: str = actor
        self._memory: dict[str, list[ChainRecord]] = {}

    # -- 冻结出口：append ---------------------------------------------------

    async def append(
        self,
        *,
        payload: Mapping[str, Any],
        run_id: str | None = None,
        seq: int | None = None,
    ) -> str:
        """追加一条审计记录，返回本条哈希。

        :param payload: 负载（会被 canonical_json 序列化）。
        :param run_id: 归属 Run；``None`` 记入全局链。
        :param seq: 显式序号（重放用）；缺省自动续号。
        :returns: 本条哈希（64 位 hex）。
        """
        key = run_id or "__global__"
        previous = await self._last_hash(key)
        record_seq = seq if seq is not None else await self._next_seq(key)
        record_payload = dict(payload)
        digest = chain_hash(previous, record_payload)
        record = ChainRecord(
            seq=record_seq,
            prev_hash=previous or GENESIS_HASH,
            hash=digest,
            payload=record_payload,
            created_at=get_clock().now().isoformat(),
        )
        if self._session_factory is None:
            self._memory.setdefault(key, []).append(record)
        else:
            await self._persist(key, record)
        return digest

    # -- 冻结出口：verify ---------------------------------------------------

    async def verify(self, *, run_id: str | None = None) -> ChainVerifyReport:
        """校验链完整性。

        :param run_id: 校验范围；``None`` 校验全局链。
        :returns: :class:`ChainVerifyReport`。
        """
        key = run_id or "__global__"
        records = await self._records(key)
        expected_prev: str | None = None
        verified = 0
        for record in records:
            expected_hash = chain_hash(expected_prev, record.payload)
            prev_ok = expected_prev is None or record.prev_hash == expected_prev
            if not prev_ok or record.hash != expected_hash:
                return ChainVerifyReport(
                    total=len(records),
                    verified=verified,
                    ok=False,
                    broken_at=record.seq,
                    broken_expected_hash=expected_hash,
                    broken_actual_hash=record.hash,
                    message=f"链在第 {record.seq} 条损坏",
                )
            expected_prev = record.hash
            verified += 1
        return ChainVerifyReport(
            total=len(records),
            verified=verified,
            ok=True,
            message="审计链完整",
        )

    # -- 内部 ---------------------------------------------------------------

    async def _last_hash(self, key: str) -> str | None:
        if self._session_factory is None:
            records = self._memory.get(key)
            return records[-1].hash if records else None
        records = await self._records(key)
        return records[-1].hash if records else None

    async def _next_seq(self, key: str) -> int:
        if self._session_factory is None:
            records = self._memory.get(key)
            return (records[-1].seq + 1) if records else 0
        records = await self._records(key)
        return (records[-1].seq + 1) if records else 0

    async def _records(self, key: str) -> list[ChainRecord]:
        if self._session_factory is None:
            return list(self._memory.get(key, []))
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            rows = await uow.audit_logs.list_for_resource("audit_chain", key, limit=100_000)
        parsed: list[ChainRecord] = []
        for row in rows:
            chain = (row.after or {}).get("chain")
            if isinstance(chain, dict):
                parsed.append(ChainRecord.model_validate(chain))
        parsed.sort(key=lambda record: record.seq)
        return parsed

    async def _persist(self, key: str, record: ChainRecord) -> None:
        async with UnitOfWork(session_factory=self._session_factory) as uow:
            await uow.audit_logs.append(
                action="audit.chain_append",
                resource_type="audit_chain",
                actor=self._actor,
                actor_type="system",
                resource_id=key,
                after={"chain": record.model_dump()},
                reason=f"seq={record.seq}",
            )
