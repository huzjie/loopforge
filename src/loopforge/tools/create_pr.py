"""``create_pr`` 工具 —— 创建 Pull Request（``PROBEABLE``）。

双模式
------
* **本地沙箱模式（默认，自托管无 GitHub 时）**：把 PR 草稿写入工作区
  ``.loopforge/prs/<branch>.json``，body 内嵌 ``<!-- lf-step-key:<key> -->``
  注释（§8.1.5 强制约定），probe 检查文件存在 + key 匹配。
* **gh CLI 模式**：检测到 ``gh`` 且远端为 GitHub 时，用 ``gh pr create``
  创建真实 PR，body 同样内嵌 key 注释；probe 用 ``gh pr view`` 查询。
"""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, Field

from loopforge.core.enums import SideEffectClass
from loopforge.tools.base import BaseTool, ProbeResult, StepIntent, ToolContext, ToolResult

__all__ = ["CreatePrArgs", "CreatePrTool"]

_PR_MARKER = "<!-- lf-step-key:{key} -->"


class CreatePrArgs(BaseModel):
    """创建 PR 参数。"""

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=16000)
    head: str = Field(min_length=1, description="源分支")
    base: str = Field(default="main", description="目标分支")
    remote: str = Field(default="origin")
    use_gh: bool = Field(default=False, description="强制用 gh CLI（否则自动探测）")


class CreatePrTool(BaseTool):
    """创建 Pull Request。"""

    name: ClassVar[str] = "create_pr"
    description: ClassVar[str] = (
        "创建 Pull Request。自托管环境默认写入本地 PR 草稿；检测到 gh CLI 且远端为"
        "GitHub 时创建真实 PR。body 自动内嵌 lf-step-key 注释用于幂等探测。"
    )
    args_schema: ClassVar[type[BaseModel]] = CreatePrArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.PROBEABLE

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """创建 PR。"""
        parsed = args.model_dump()
        if ctx.executor is None or ctx.workspace is None:
            return ToolResult.failure("executor / workspace 未注入", error_code="LF-TOOL-0902")

        body = parsed["body"] + f"\n\n{_PR_MARKER.format(key=ctx.idempotency_key)}"
        use_gh = parsed["use_gh"] or await self._gh_available(ctx, parsed["remote"])

        if use_gh:
            return await self._create_via_gh(ctx, parsed, body)
        return await self._create_local(ctx, parsed, body)

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """探测 PR 是否已创建。"""
        if ctx.executor is None or ctx.workspace is None:
            return ProbeResult(executed=False, confidence="low", evidence="executor 未注入")
        head = str(intent.args.get("head", ""))
        key = intent.idempotency_key or str(intent.args.get("key", ""))
        use_gh = bool(intent.args.get("use_gh", False)) or await self._gh_available(
            ctx, str(intent.args.get("remote", "origin"))
        )
        if use_gh:
            return await self._probe_gh(ctx, head, key)
        return await self._probe_local(ctx, head, key)

    # ------------------------------------------------------------------ 本地模式

    async def _create_local(self, ctx: ToolContext, parsed: dict, body: str) -> ToolResult:
        """写本地 PR 草稿。"""
        rel = f".loopforge/prs/{parsed['head']}.json"
        payload = {
            "title": parsed["title"],
            "body": body,
            "head": parsed["head"],
            "base": parsed["base"],
            "run_id": ctx.run_id,
            "task_id": ctx.task_id,
            "step_id": ctx.step_id,
            "idempotency_key": ctx.idempotency_key,
        }
        try:
            stat = await ctx.executor.write_file(
                ctx.workspace, rel, json.dumps(payload, ensure_ascii=False, indent=2)
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(
                f"写 PR 草稿失败：{exc}",
                error_code=getattr(exc, "code", "LF-TOOL-0902"),
            )
        ref = f"local://{rel}"
        return ToolResult(
            ok=True,
            output=f"已创建本地 PR 草稿：{rel}\n标题：{parsed['title']}",
            data={"mode": "local", "path": rel, "head": parsed["head"]},
            external_ref=ref,
        )

    async def _probe_local(self, ctx: ToolContext, head: str, key: str) -> ProbeResult:
        """按本地草稿文件探测。"""
        rel = f".loopforge/prs/{head}.json"
        try:
            content = await ctx.executor.read_file(ctx.workspace, rel)
        except Exception:  # noqa: BLE001
            return ProbeResult(executed=False, confidence="medium", evidence=f"草稿不存在：{rel}")
        executed = key and key in content.content
        return ProbeResult(
            executed=executed,
            external_ref=f"local://{rel}",
            confidence="high" if executed else "low",
            evidence=f"草稿存在且{'含' if executed else '不含'}幂等键 {key}",
        )

    # ------------------------------------------------------------------ gh 模式

    async def _create_via_gh(self, ctx: ToolContext, parsed: dict, body: str) -> ToolResult:
        """用 gh CLI 创建真实 PR。"""
        argv = [
            "gh",
            "pr",
            "create",
            "--title",
            parsed["title"],
            "--body",
            body,
            "--base",
            parsed["base"],
            "--head",
            parsed["head"],
            "--repo",
            parsed["remote"],
        ]
        try:
            result = await ctx.executor.run(
                self._spec(ctx, argv, parsed["remote"] if False else ".")
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(f"gh pr create 失败：{exc}", error_code="LF-TOOL-0902")
        if not result.ok:
            return ToolResult.failure(
                f"gh pr create 失败（exit={result.exit_code}）\n{result.stderr[:2000]}",
                error_code="LF-TOOL-0902",
            )
        url = result.stdout.strip()
        return ToolResult(
            ok=True,
            output=f"已创建 PR：{url}",
            data={"mode": "gh", "url": url, "head": parsed["head"]},
            external_ref=url,
        )

    async def _probe_gh(self, ctx: ToolContext, head: str, key: str) -> ProbeResult:
        """用 gh CLI 探测。"""
        try:
            result = await ctx.executor.run(
                self._spec(ctx, ["gh", "pr", "view", head, "--json", "url,body"], ".")
            )
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(executed=False, confidence="low", evidence=f"gh 异常：{exc}")
        if not result.ok:
            return ProbeResult(executed=False, confidence="medium", evidence=f"gh pr view 未找到 {head}")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ProbeResult(executed=False, confidence="low", evidence="gh 输出无法解析")
        executed = key and key in data.get("body", "")
        return ProbeResult(
            executed=executed,
            external_ref=data.get("url"),
            confidence="high" if executed else "medium",
            evidence=f"PR {head} 存在且{'含' if executed else '不含'}幂等键",
        )

    async def _gh_available(self, ctx: ToolContext, remote: str) -> bool:
        """gh CLI 是否可用。"""
        try:
            result = await ctx.executor.run(self._spec(ctx, ["gh", "--version"], "."))
            return result.ok
        except Exception:  # noqa: BLE001
            return False

    def _spec(self, ctx: ToolContext, argv: list[str], cwd: str) -> object:
        """构造 CommandSpec。"""
        from loopforge.executors.base import CommandSpec

        return CommandSpec(handle_id=ctx.workspace.id, argv=argv, cwd=cwd, timeout_s=60.0)
