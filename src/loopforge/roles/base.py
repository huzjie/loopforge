"""``BaseRole`` 抽象 —— 多角色 Agent 的统一契约（§11.9 冻结）。

来源：``docs/TASK_BREAKDOWN.md`` §11.9 / ``docs/ARCHITECTURE.md`` §7.5。

冻结签名::

    class BaseRole(abc.ABC):
        name: ClassVar[RoleName]
        allowed_tools: ClassVar[tuple[str, ...]]
        prompt_file: ClassVar[str]

        def __init__(self, binding: RoleBinding, provider: BaseProvider) -> None: ...

        @abc.abstractmethod
        def build_messages(self, ctx: RoleContext) -> list[Message]: ...

        @abc.abstractmethod
        def parse_output(self, result: ChatResult) -> RoleOutput: ...

        async def act(self, ctx: RoleContext) -> RoleOutput: ...

设计要点
--------
1. **``allowed_tools`` 是硬边界**（§8.3-1）：``REVIEWER`` 拿不到任何写工具，
   ``SUMMARIZER`` 是纯文本角色。运行时由
   :func:`assert_role_can_tool` / :func:`role_tool_names` 强制，**不是靠 prompt
   里写「请不要修改文件」**。越权调用抛 ``LF-ROLE-1602`` 并发布
   ``POLICY_DENIED`` 事件。
2. **输出必须结构化解析**（§8.3-2）：:meth:`parse_output` 返回强类型
   :class:`RoleOutput`，解析失败抛 ``LF-ROLE-1601``，由 WP4 决定重试或降级。
   禁止把 LLM 原始文本直接透传给下游。
3. **Prompt 必须版本化**（§8.3-3）：版本号与内容 hash 由
   :mod:`loopforge.roles.prompts` 计算，写入 ``LlmCall`` 便于 A/B 与回归定位。
4. 时间一律走 :class:`loopforge.core.clock.Clock`；角色层不直接调用
   ``datetime.now()``。
"""

from __future__ import annotations

import abc
import asyncio
import json
import re
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import PolicyDecision, RoleName
from loopforge.core.error_codes import ErrorCode
from loopforge.core.errors import RoleError
from loopforge.core.event_bus import EventBus, get_event_bus
from loopforge.core.event_types import EventType, PolicyDecisionPayload
from loopforge.providers.types import (
    ChatRequest,
    ChatResult,
    Message,
    ToolCallRequest,
    Usage,
)

__all__ = [
    "RoleContext",
    "RoleOutput",
    "TaskDraft",
    "MilestoneDraft",
    "RoleBindingLike",
    "BaseRole",
    "role_tool_names",
    "assert_role_can_tool",
    "can_role_use_tool",
    "publish_tool_denied",
    "publish_tool_denied_async",
    "FROZEN_ROLE_TOOLS",
    "RoleOutputParseError",
    "RoleToolForbiddenError",
    "RolePromptMissingError",
    "RoleUnavailableError",
]


class RoleOutputParseError(RoleError):
    """角色输出解析失败（``LF-ROLE-1601``）。"""

    default_code = ErrorCode.ROLE_OUTPUT_PARSE_FAILED


class RoleToolForbiddenError(RoleError):
    """角色越权调用工具（``LF-ROLE-1602``）。"""

    default_code = ErrorCode.ROLE_TOOL_FORBIDDEN


class RolePromptMissingError(RoleError):
    """Prompt 模板缺失（``LF-ROLE-1603``）。"""

    default_code = ErrorCode.ROLE_PROMPT_MISSING


class RoleUnavailableError(RoleError):
    """角色当前不可用 / 越权进入非法阶段（``LF-ROLE-1604``）。"""

    default_code = ErrorCode.ROLE_UNAVAILABLE

#: §11.9 冻结的角色 → 工具白名单（只读，一个字符不能改）。
#: 这是 `ToolRegistry.for_role()` 之外的第二道硬边界：即使注册表配置被改，
#: 角色自身的 ``allowed_tools`` 也不会跟着漂移。
FROZEN_ROLE_TOOLS: Final[dict[RoleName, tuple[str, ...]]] = {
    RoleName.PLANNER: ("read_file", "list_dir", "search_code"),
    RoleName.CODER: (
        "read_file",
        "list_dir",
        "search_code",
        "write_file",
        "edit_file",
        "apply_patch",
        "run_shell",
        "run_tests",
        "git_ops",
    ),
    RoleName.REVIEWER: ("read_file", "list_dir", "search_code"),  # 零写权限
    RoleName.TESTER: ("read_file", "list_dir", "search_code", "write_file", "run_tests", "run_shell"),
    RoleName.SUMMARIZER: (),  # 纯文本角色，不调工具
}


class RoleContext(BaseModel):
    """一次角色执行的完整上下文（WP4 编排引擎构造后传入）。

    Attributes:
        run_id: Run ID。
        task_id: 当前 Task ID；规划阶段可为 ``None``。
        goal: Run 的总目标（规划阶段必有）。
        task_title / task_body: 当前任务的说明（执行阶段必有）。
        context_pack: WP5 ``ContextManager.build_context()`` 的产物；
            为 ``None`` 时角色按自身 prompt + 目标自行组织消息。
        previous_output: 上一轮角色输出（Reviewer 审 Coder、Tester 测 Coder 用）。
        loop_index: 当前循环轮数（MockProvider 确定性派生用）。
        trace_id: 链路 ID。
        metadata: 自由扩展位（角色可读，不落库）。
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    goal: str = Field(default="", description="Run 总目标")
    task_title: str = Field(default="", max_length=500)
    task_body: str = Field(default="", description="任务正文")
    context_pack: Any | None = Field(default=None, description="ContextPack 或 None")
    previous_output: "RoleOutput | None" = Field(default=None)
    loop_index: int = Field(default=0, ge=0)
    trace_id: str = Field(default="", max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_kwargs(cls, **kwargs: Any) -> "RoleContext":
        """宽松构造（未提供的字段给默认值）。"""
        return cls(**{k: v for k, v in kwargs.items() if v is not None})


class RoleOutput(BaseModel):
    """角色输出 —— **字段逐字冻结**（``docs/ARCHITECTURE.md`` §7.5）。

    Attributes:
        role: 产出该输出的角色。
        text: 给下游 / 用户的自然语言说明。
        tool_calls: 模型请求的工具调用（执行阶段由 WP4 逐条执行）。
        structured: 角色专属强类型结果的 dict 形态 —— Planner→``PlanResult``，
            Reviewer→``ReviewVerdict``，Coder→``CodeChangeResult``，
            Tester→``TestReport``，Summarizer→``SummaryResult``。
        pinned_constraints: 角色显式追加的 Pinned Constraint（Planner 必有）。
        handoff_to: 建议交接给的角色；``None`` 表示由 RoleRouter 决定。
        usage: token 用量（透传自 ``ChatResult``）。
        finished: 该角色是否认为本阶段工作完成。
    """

    model_config = ConfigDict(extra="forbid")

    role: RoleName
    text: str = ""
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    structured: dict[str, Any] = Field(default_factory=dict)
    pinned_constraints: list[str] = Field(default_factory=list)
    handoff_to: RoleName | None = None
    usage: Usage = Field(default_factory=Usage)
    finished: bool = False

    def structured_as(self, model: type[BaseModel]) -> BaseModel | None:
        """把 ``structured`` 转成强类型模型；字段不符返回 ``None``。"""
        try:
            return model.model_validate(self.structured)
        except Exception:  # noqa: BLE001 - 畸形结构按 None 处理
            return None


class TaskDraft(BaseModel):
    """Planner 产出的任务草稿（可被 WP4 落库为 Task）。

    Attributes:
        task_key: Run 内稳定短标识（如 ``T-0001``）。
        title: 任务标题。
        body: 任务正文（验收标准、约束、上下文）。
        kind: 任务类型 —— 决定 RoleRouter 选角（``code`` / ``test`` / ``docs``
            / ``refactor`` / ``fix`` / ``review`` / ``other``）。
        priority: 优先级，数值越大越优先（Task 表同语义）。
        depends_on: 前置任务 key 列表（DAG 边）。
        touch_paths: 预计改动的路径（冲突检测用）。
        acceptance: 验收标准列表。
    """

    model_config = ConfigDict(extra="forbid")

    task_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="")
    kind: str = Field(default="code", pattern="^(code|test|docs|refactor|fix|review|other)$")
    priority: int = Field(default=100, ge=0)
    depends_on: list[str] = Field(default_factory=list)
    touch_paths: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)


class MilestoneDraft(BaseModel):
    """里程碑定义：一批任务的「可验证中间成果」。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="")
    task_keys: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)


class RoleBindingLike(BaseModel):
    """角色 → 模型绑定（``RoleBinding`` 配置的内存形态，避免反向依赖 config）。"""

    model_config = ConfigDict(extra="forbid")

    provider: str = "mock"
    model: str = "mock-large"
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)


def role_tool_names(role: RoleName) -> tuple[str, ...]:
    """返回角色冻结的工具白名单（§11.9）。"""
    return FROZEN_ROLE_TOOLS.get(role, ())


def can_role_use_tool(role: RoleName, tool_name: str) -> bool:
    """角色是否被允许使用某工具（冻结白名单判定）。"""
    return tool_name in FROZEN_ROLE_TOOLS.get(role, ())


def publish_tool_denied(
    *,
    role: RoleName,
    tool_name: str,
    reason: str,
    run_id: str | None = None,
    task_id: str | None = None,
    step_id: str | None = None,
    bus: EventBus | None = None,
) -> None:
    """发布 ``POLICY_DENIED`` 事件（越权拦截的可观测凭据）。

    同步入口：检测到运行中的事件循环时以 fire-and-forget 方式调度
    :func:`publish_tool_denied_async`；无事件循环（纯同步调用）时静默跳过，
    不阻塞工具边界检查。需要确定性断言时请直接 await
    :func:`publish_tool_denied_async`。

    Args:
        role: 越权角色。
        tool_name: 被拒工具名。
        reason: 拒绝原因。
        run_id / task_id / step_id: 上下文 ID（可选）。
        bus: 事件总线；``None`` 取全局单例（未启动则静默跳过）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # 无事件循环：跳过（边界检查本身不依赖事件总线）
    try:
        asyncio.ensure_future(
            publish_tool_denied_async(
                role=role,
                tool_name=tool_name,
                reason=reason,
                run_id=run_id,
                task_id=task_id,
                step_id=step_id,
                bus=bus,
            )
        )
    except Exception:  # noqa: BLE001 - 调度失败不影响主流程
        pass


async def publish_tool_denied_async(
    *,
    role: RoleName,
    tool_name: str,
    reason: str,
    run_id: str | None = None,
    task_id: str | None = None,
    step_id: str | None = None,
    bus: EventBus | None = None,
) -> None:
    """异步发布 ``POLICY_DENIED`` 事件（确定性可断言版本）。"""
    target = bus if bus is not None else get_event_bus()
    if target is None:
        return
    payload = PolicyDecisionPayload(
        run_id=run_id,
        task_id=task_id,
        step_id=step_id,
        action_kind="tool_call",
        decision=PolicyDecision.DENY,
        rule_id="role.boundary.allowed_tools",
        reason=reason,
        category="role",
    )
    try:
        await target.publish(
            EventType.POLICY_DENIED,
            payload,
            run_id=run_id,
            task_id=task_id,
        )
    except Exception:  # noqa: BLE001 - 事件总线故障不应阻断工具调用主流程
        pass


def assert_role_can_tool(
    role: RoleName,
    tool_name: str,
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    step_id: str | None = None,
    bus: EventBus | None = None,
    raise_on_deny: bool = True,
) -> bool:
    """角色工具边界守卫（G3 闸门 #6 的强制点）。

    Args:
        role: 请求角色。
        tool_name: 请求工具名。
        run_id / task_id / step_id: 上下文（写入 POLICY_DENIED 事件）。
        bus: 事件总线（``None`` 取全局单例）。
        raise_on_deny: 为 ``True`` 时越权抛 :class:`RoleError`
            （``LF-ROLE-1602``）；为 ``False`` 时仅发布事件并返回 ``False``。

    Returns:
        是否放行。

    Raises:
        RoleError: 越权且 ``raise_on_deny=True``。
    """
    if can_role_use_tool(role, tool_name):
        return True
    reason = (
        f"角色 {role.value} 不允许调用工具 {tool_name!r}（冻结白名单："
        f"{', '.join(FROZEN_ROLE_TOOLS.get(role, ())) or '无'}）"
    )
    publish_tool_denied(
        role=role,
        tool_name=tool_name,
        reason=reason,
        run_id=run_id,
        task_id=task_id,
        step_id=step_id,
        bus=bus,
    )
    if raise_on_deny:
        raise RoleToolForbiddenError(
            reason,
            details={
                "role": role.value,
                "tool_name": tool_name,
                "allowed_tools": list(FROZEN_ROLE_TOOLS.get(role, ())),
            },
        )
    return False


class BaseRole(abc.ABC):
    """智能体角色抽象基类（§11.9 冻结契约）。

    Args:
        binding: 角色模型绑定（provider / model / temperature / max_tokens）。
        provider: 已实例化的 :class:`~loopforge.providers.base.BaseProvider`。
    """

    #: 角色名（冻结）。
    name: ClassVar[RoleName]
    #: 角色可用工具白名单（冻结，见 §11.9 表格）。
    allowed_tools: ClassVar[tuple[str, ...]]
    #: Prompt 模板文件名（``prompts/<prompt_file>``）。
    prompt_file: ClassVar[str] = ""

    def __init__(self, binding: RoleBindingLike, provider: Any) -> None:
        self.binding = binding
        self.provider = provider

    # ------------------------------------------------------------------ 抽象

    @abc.abstractmethod
    def build_messages(self, ctx: RoleContext) -> list[Message]:
        """把上下文组装成消息列表（system + user）。

        Args:
            ctx: 角色执行上下文。

        Returns:
            可直接投喂 Provider 的消息列表。
        """

    @abc.abstractmethod
    def parse_output(self, result: ChatResult) -> RoleOutput:
        """把 Provider 返回解析为强类型 :class:`RoleOutput`。

        Args:
            result: Provider 的非流式响应。

        Returns:
            结构化角色输出。

        Raises:
            RoleError: 输出畸形（``LF-ROLE-1601``）。
        """

    # ------------------------------------------------------------------ 默认实现

    async def act(self, ctx: RoleContext) -> RoleOutput:
        """默认执行流程：组装消息 → 调 Provider → 解析输出。

        子类可覆盖以注入工具调用、重试或流式逻辑。

        Args:
            ctx: 角色执行上下文。

        Returns:
            结构化角色输出。
        """
        messages = self.build_messages(ctx)
        request = ChatRequest(
            model=self.binding.model,
            messages=messages,
            temperature=self.binding.temperature,
            max_tokens=self.binding.max_tokens,
            metadata={
                "run_id": ctx.run_id,
                "task_id": ctx.task_id or "",
                "role": self.name.value,
                "loop_index": str(ctx.loop_index),
                "trace_id": ctx.trace_id,
            },
        )
        result = await self.provider.chat(request)
        return self.parse_output(result)

    # ------------------------------------------------------------------ 解析工具

    @staticmethod
    def extract_json(text: str) -> dict[str, Any]:
        """从 LLM 输出中鲁棒提取 JSON 对象。

        容忍：markdown 代码块包裹、前后解释文字、截断 JSON 尾部的杂散字符。

        Args:
            text: 原始输出。

        Returns:
            解析出的 dict。

        Raises:
            RoleError: 找不到合法 JSON 对象。
        """
        raw = (text or "").strip()
        if not raw:
            raise RoleError("角色输出为空，无法解析 JSON", details={"role_hint": "unknown"})
        # 1) 优先剥掉 ```json ... ``` 代码块
        fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
        candidates: list[str] = []
        if fenced:
            candidates.append(fenced.group(1).strip())
        # 2) 从第一个 { 到最后一个 }（容忍前后解释文字）
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            candidates.append(raw[start : end + 1])
        candidates.append(raw)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed
        raise RoleError(
            "角色输出无法解析为 JSON 对象",
            details={"sample": raw[:400], "role_hint": "unknown"},
        )

    @staticmethod
    def split_fence(text: str) -> str:
        """剥掉首尾的 markdown 代码围栏（解析失败的兜底归一）。"""
        raw = (text or "").strip()
        fenced = re.search(r"```(?:[A-Za-z0-9_+-]*)\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            return fenced.group(1).strip()
        return raw
