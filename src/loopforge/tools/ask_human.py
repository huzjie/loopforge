"""``ask_human`` 工具 —— 向人工请求审批/澄清（``PROBEABLE``）。

执行语义
--------
* 有 DB（``UnitOfWork`` + ``approvals`` 仓储）时：写入一条 ``ask_human``
  审批记录（``resource_id=step_id``），返回记录 ID。
* 无 DB（测试 / 沙箱）时：写入工作区 ``.loopforge/approvals/<step_id>.json``，
  返回文件引用。

probe 语义
----------
查询 approvals 表（或本地文件）是否已有同 ``step_id`` 的记录 ——
「人工是否已回应」由 WP4/编排层轮询判定，probe 只回答「请求是否已落库」。
"""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, Field

from loopforge.core.enums import SideEffectClass
from loopforge.tools.base import BaseTool, ProbeResult, StepIntent, ToolContext, ToolResult

__all__ = ["AskHumanArgs", "AskHumanTool"]


class AskHumanArgs(BaseModel):
    """请求人工参数。"""

    question: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=8000)
    require_approval: bool = Field(default=False, description="是否必须显式批准才能继续")


class AskHumanTool(BaseTool):
    """向人工请求审批或澄清。"""

    name: ClassVar[str] = "ask_human"
    description: ClassVar[str] = (
        "向人工请求审批或澄清。执行会落一条审批记录（DB 或工作区文件），"
        "返回记录引用供后续轮询。"
    )
    args_schema: ClassVar[type[BaseModel]] = AskHumanArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.PROBEABLE

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """落一条审批请求。"""
        parsed = args.model_dump()
        if ctx.executor is None or ctx.workspace is None:
            return ToolResult.failure("executor / workspace 未注入", error_code="LF-TOOL-0902")

        record = {
            "step_id": ctx.step_id,
            "run_id": ctx.run_id,
            "task_id": ctx.task_id,
            "question": parsed["question"],
            "context": parsed["context"],
            "require_approval": parsed["require_approval"],
            "status": "pending",
            "created_at": ctx.idempotency_key,
        }

        # 优先写 DB（governance 交付后启用）。
        db_ref = await self._write_db(record)
        if db_ref is not None:
            return ToolResult(
                ok=True,
                output=f"已创建人工审批请求 #{db_ref}：{parsed['question']}",
                data={"approval_id": db_ref, "status": "pending"},
                external_ref=str(db_ref),
            )

        # 兜底：写工作区文件。
        rel = f".loopforge/approvals/{ctx.step_id}.json"
        try:
            await ctx.executor.write_file(
                ctx.workspace, rel, json.dumps(record, ensure_ascii=False, indent=2)
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(
                f"写审批记录失败：{exc}",
                error_code=getattr(exc, "code", "LF-TOOL-0902"),
            )
        ref = f"local://{rel}"
        return ToolResult(
            ok=True,
            output=f"已创建人工审批请求（本地）：{rel}\n问题：{parsed['question']}",
            data={"path": rel, "status": "pending"},
            external_ref=ref,
        )

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """查询同一 step 是否已有审批记录。"""
        step_id = intent.step_id or str(intent.args.get("step_id", ""))
        if ctx.executor is None or ctx.workspace is None:
            return ProbeResult(executed=False, confidence="low", evidence="executor 未注入")

        # DB 探测（governance 交付后启用）。
        db_probe = await self._probe_db(step_id)
        if db_probe is not None:
            return db_probe

        rel = f".loopforge/approvals/{step_id}.json"
        try:
            content = await ctx.executor.read_file(ctx.workspace, rel)
        except Exception:  # noqa: BLE001
            return ProbeResult(executed=False, confidence="medium", evidence=f"审批记录不存在：{rel}")
        return ProbeResult(
            executed=True,
            external_ref=f"local://{rel}",
            confidence="high",
            evidence=f"审批记录存在：{rel}",
        )

    # ------------------------------------------------------------------ 内部

    async def _write_db(self, record: dict) -> str | None:
        """写 DB 审批记录；无仓储时返回 ``None``。"""
        try:
            from loopforge.db.uow import UnitOfWork  # noqa: PLC0415 - 延迟导入避免硬依赖

            async with UnitOfWork() as uow:
                created = await uow.approvals.append(
                    action_kind="ask_human",
                    resource_type="step",
                    resource_id=record["step_id"],
                    requested_by="agent",
                    reason=record["question"],
                    metadata={"run_id": record["run_id"], "task_id": record["task_id"]},
                )
                await uow.commit()
                return str(getattr(created, "id", ""))
        except Exception:  # noqa: BLE001 - 无 DB / 无仓储时降级到本地文件
            return None

    async def _probe_db(self, step_id: str) -> ProbeResult | None:
        """DB 探测；无仓储时返回 ``None``。"""
        try:
            from loopforge.db.uow import UnitOfWork  # noqa: PLC0415

            async with UnitOfWork() as uow:
                records = await uow.approvals.list_for_resource(
                    resource_type="step", resource_id=step_id
                )
        except Exception:  # noqa: BLE001 - 无 DB 时降级
            return None
        if not records:
            return ProbeResult(
                executed=False, confidence="medium", evidence=f"DB 无 {step_id} 审批记录"
            )
        return ProbeResult(
            executed=True,
            external_ref=str(getattr(records[0], "id", "")),
            confidence="high",
            evidence=f"DB 存在 {len(records)} 条 {step_id} 审批记录",
        )
