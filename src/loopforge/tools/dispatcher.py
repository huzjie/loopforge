"""工具调用分发器 —— 按名分发、参数校验、结果规范化、异常转错误码。

调用姿势（WP4 / WP6 标准）::

    from loopforge.tools import ToolRegistry, ToolDispatcher

    registry = ToolRegistry()
    registry.autoload_builtins()
    dispatcher = ToolDispatcher(registry)

    result = await dispatcher.dispatch(ctx, "write_file", {"path": "a.txt", "content": "hi"})
    # result.ok == True / False，绝不抛裸异常

设计
----
* 未注册 → ``LF-TOOL-0903``；参数校验失败 → ``LF-TOOL-0901``；
  角色越权 → ``LF-TOOL-0904``；执行异常 → ``LF-TOOL-0902``。
* :meth:`dispatch` 不抛 ``LoopForgeError`` 之外的异常：所有异常都转成
  :class:`~loopforge.tools.base.ToolResult.failure`。
* :meth:`dispatch_checked` 在失败时抛 ``ToolError``（给需要异常语义的调用方）。
"""

from __future__ import annotations

from typing import Any

from loopforge.core.enums import RoleName
from loopforge.core.errors import LoopForgeError, ToolError
from loopforge.core.logging import get_logger
from loopforge.tools.base import BaseTool, ToolContext, ToolResult
from loopforge.tools.registry import ToolRegistry

__all__ = ["ToolDispatcher"]

_log = get_logger(__name__)


class ToolDispatcher:
    """工具分发器。

    Args:
        registry: 工具注册表。
        role: 默认角色（用于 ``for_role`` 边界校验；``None`` 跳过）。
    """

    def __init__(self, registry: ToolRegistry, *, role: RoleName | None = None) -> None:
        self._registry: ToolRegistry = registry
        self._role: RoleName | None = role

    # ------------------------------------------------------------------ 分发

    async def dispatch(
        self,
        ctx: ToolContext,
        tool_name: str,
        args: dict[str, Any],
        *,
        role: RoleName | None = None,
        confirmed: bool = False,
    ) -> ToolResult:
        """分发一次工具调用（绝不抛裸异常）。

        Args:
            ctx: 工具上下文。
            tool_name: 工具名。
            args: 原始参数字典。
            role: 角色（覆盖构造时的默认角色）。
            confirmed: 是否已有人工确认（``requires_confirmation`` 工具需要）。

        Returns:
            :class:`ToolResult`。
        """
        tool = self._registry.get_or_none(tool_name)
        if tool is None:
            return ToolResult.failure(
                f"工具未注册：{tool_name}",
                error_code="LF-TOOL-0903",
                tool_name=tool_name,
            )

        active_role = role or self._role
        if active_role is not None and tool not in self._registry.for_role(active_role):
            return ToolResult.failure(
                f"角色 {active_role.value} 无权调用 {tool_name}",
                error_code="LF-TOOL-0904",
                tool_name=tool_name,
            )

        if tool.requires_confirmation and not confirmed:
            return ToolResult.failure(
                f"工具 {tool_name} 需要显式确认后才能执行",
                error_code="LF-TOOL-0905",
                tool_name=tool_name,
            )

        try:
            parsed = tool.validate_args(args)
        except Exception as exc:  # noqa: BLE001 - 参数校验失败统一转 0901
            _log.warning("tool_args_invalid", tool=tool_name, error=str(exc))
            return ToolResult.failure(
                f"参数校验失败：{exc}",
                error_code="LF-TOOL-0901",
                tool_name=tool_name,
            )

        try:
            return await tool.execute(ctx, parsed)
        except LoopForgeError as exc:
            return ToolResult.failure(
                f"工具执行失败：{exc}",
                error_code=str(exc.code),
                tool_name=tool_name,
            )
        except Exception as exc:  # noqa: BLE001 - 未知异常兜底
            _log.error("tool_execution_crashed", tool=tool_name, error=str(exc))
            return ToolResult.failure(
                f"工具执行异常：{exc}",
                error_code="LF-TOOL-0902",
                tool_name=tool_name,
            )

    async def dispatch_checked(
        self,
        ctx: ToolContext,
        tool_name: str,
        args: dict[str, Any],
        *,
        role: RoleName | None = None,
        confirmed: bool = False,
    ) -> ToolResult:
        """分发并要求成功；失败抛 :class:`ToolError`。

        Args:
            ctx: 工具上下文。
            tool_name: 工具名。
            args: 参数字典。
            role: 角色。
            confirmed: 是否已确认。

        Returns:
            成功的 :class:`ToolResult`。

        Raises:
            ToolError: 工具执行失败。
        """
        result = await self.dispatch(ctx, tool_name, args, role=role, confirmed=confirmed)
        if not result.ok:
            raise ToolError(
                f"工具 {tool_name} 执行失败：{result.output}",
                details={"error_code": result.error_code, "data": result.data},
            )
        return result

    # ------------------------------------------------------------------ 探测

    async def probe_tool(
        self, ctx: ToolContext, tool_name: str, intent: Any
    ) -> Any:
        """分发一次幂等探测。

        Args:
            ctx: 工具上下文。
            tool_name: 工具名。
            intent: :class:`~loopforge.tools.base.StepIntent`。

        Returns:
            :class:`~loopforge.tools.base.ProbeResult`。
        """
        tool = self._registry.get_or_none(tool_name)
        if tool is None:
            from loopforge.tools.base import ProbeResult

            return ProbeResult(executed=False, confidence="unknown", evidence=f"工具未注册 {tool_name}")
        return await tool.probe(ctx, intent)
