"""Provider 层数据传输对象 —— **字段名逐字冻结**。

冻结依据：``docs/ARCHITECTURE.md`` §7.1 与 ``docs/TASK_BREAKDOWN.md`` §11.3。

下游（WP4 编排引擎 / WP5 治理 / WP6 角色层）只依赖本模块与
:mod:`loopforge.providers.base`，**不依赖任何具体 Provider 实现**。

设计约束
--------
1. 全部使用 Pydantic v2，``extra="forbid"``，避免上游偷偷塞字段导致方言漂移；
2. 金额一律 :class:`decimal.Decimal`，禁止 ``float``；
3. 时间一律 tz-aware UTC，且由 :class:`loopforge.core.clock.Clock` 提供；
4. :class:`ProviderBinding` 用 ``dataclass`` 而非 Pydantic —— 它持有**活的
   Provider 实例**，Pydantic 校验器对其无意义且会引入循环导入。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from loopforge.core.enums import RoleName

if TYPE_CHECKING:  # pragma: no cover —— 仅为类型标注，运行期不导入以打断循环
    from loopforge.providers.base import BaseProvider

__all__ = [
    "MessageRole",
    "FinishReason",
    "ToolChoice",
    "Message",
    "ToolSpec",
    "ToolCallRequest",
    "ChatRequest",
    "Usage",
    "ChatResult",
    "StreamChunk",
    "ProviderCapabilities",
    "ProviderHealth",
    "ProviderConfigModel",
    "RoutingHint",
    "ProviderBinding",
    "EmbeddingRequest",
    "EmbeddingResult",
    "FaultKind",
    "FaultSpec",
]

#: 消息角色（OpenAI 家族通用四元组）。
MessageRole = Literal["system", "user", "assistant", "tool"]

#: 结束原因（五种，跨方言归一后的值）。
FinishReason = Literal["stop", "length", "tool_calls", "content_filter", "error"]

#: 工具选择策略。
ToolChoice = Literal["auto", "none", "required"]

#: 可注入的故障种类（MockProvider 与测试用）。
FaultKind = Literal[
    "auth",
    "rate_limit",
    "timeout",
    "server_error",
    "truncate",
    "content_filter",
    "bad_response",
]


class _Dto(BaseModel):
    """Provider DTO 基类：禁止未知字段，保证方言转换是显式的。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# 消息与工具
# ---------------------------------------------------------------------------


class ToolCallRequest(_Dto):
    """模型请求调用某个工具。

    Attributes:
        id: 调用 ID，回传 ``tool`` 消息时用 ``tool_call_id`` 对齐。
        name: 工具名。
        arguments: **已解析**的参数字典。解析失败时为空字典，原文见 ``raw_arguments``。
        raw_arguments: 模型吐出的原始 JSON 文本，用于排障与回放。
    """

    id: str = ""
    name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arguments: str = ""

    @classmethod
    def from_json_text(cls, *, id: str, name: str, raw: str) -> ToolCallRequest:  # noqa: A002
        """从原始 JSON 文本构造，解析失败时保留原文而不抛异常。

        Args:
            id: 调用 ID。
            name: 工具名。
            raw: 参数的 JSON 文本。

        Returns:
            构造好的 :class:`ToolCallRequest`。
        """
        parsed: dict[str, Any] = {}
        text = (raw or "").strip()
        if text:
            try:
                candidate = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                candidate = None
            if isinstance(candidate, dict):
                parsed = candidate
        return cls(id=id, name=name, arguments=parsed, raw_arguments=raw or "")

    def arguments_json(self) -> str:
        """返回稳定排序的参数 JSON 文本（用于确定性哈希与请求体构造）。

        Returns:
            紧凑且键有序的 JSON 字符串。
        """
        if self.raw_arguments:
            return self.raw_arguments
        return json.dumps(self.arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Message(_Dto):
    """一条对话消息。

    Attributes:
        role: ``system`` / ``user`` / ``assistant`` / ``tool``。
        content: 文本内容；助手发起工具调用时可为 ``None``。
        tool_calls: 助手消息携带的工具调用列表。
        tool_call_id: ``tool`` 角色消息对应的调用 ID。
        name: 工具名或说话人名。
    """

    role: MessageRole
    content: str | None = None
    tool_calls: list[ToolCallRequest] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    @classmethod
    def system(cls, content: str) -> Message:
        """快捷构造系统消息。"""
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> Message:
        """快捷构造用户消息。"""
        return cls(role="user", content=content)

    @classmethod
    def assistant(
        cls, content: str | None = None, tool_calls: list[ToolCallRequest] | None = None
    ) -> Message:
        """快捷构造助手消息。"""
        return cls(role="assistant", content=content, tool_calls=tool_calls)

    @classmethod
    def tool(cls, *, tool_call_id: str, content: str, name: str | None = None) -> Message:
        """快捷构造工具回执消息。"""
        return cls(role="tool", content=content, tool_call_id=tool_call_id, name=name)

    def text(self) -> str:
        """返回文本内容，``None`` 归一为空串。"""
        return self.content or ""


class ToolSpec(_Dto):
    """工具声明（JSON Schema 描述参数）。

    Attributes:
        name: 工具名，需与 WP3 注册的工具名一致。
        description: 给模型看的说明。
        parameters: JSON Schema 对象。
    """

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})

    @field_validator("name")
    @classmethod
    def _non_empty_name(cls, value: str) -> str:
        """工具名不能为空。"""
        if not value.strip():
            raise ValueError("ToolSpec.name 不能为空")
        return value.strip()


# ---------------------------------------------------------------------------
# 请求与响应
# ---------------------------------------------------------------------------


class ChatRequest(_Dto):
    """一次对话补全请求。

    ``metadata`` 约定键（全部可选，值为字符串）：
    ``run_id`` ``task_id`` ``step_id`` ``role`` ``trace_id`` ``loop_index``
    ``task_key`` ``attempt``。MockProvider 依赖其中的
    ``role`` / ``loop_index`` / ``task_key`` 做确定性派生。
    """

    model: str
    messages: list[Message] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    tool_choice: ToolChoice = "auto"
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    stop: list[str] = Field(default_factory=list)
    seed: int | None = None
    timeout_s: float = Field(default=120.0, gt=0.0)
    metadata: dict[str, str] = Field(default_factory=dict)

    def meta(self, key: str, default: str = "") -> str:
        """安全读取 ``metadata`` 中的某个键。

        Args:
            key: 键名。
            default: 缺省值。

        Returns:
            字符串值。
        """
        value = self.metadata.get(key)
        return value if isinstance(value, str) and value else default

    def meta_int(self, key: str, default: int = 0) -> int:
        """读取 ``metadata`` 中的整数键（解析失败回落到默认值）。

        Args:
            key: 键名。
            default: 缺省值。

        Returns:
            整数值。
        """
        raw = self.metadata.get(key)
        if raw is None:
            return default
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return default

    def role_name(self) -> RoleName | None:
        """把 ``metadata['role']`` 解析成 :class:`RoleName`。

        Returns:
            解析成功返回枚举，否则 ``None``。
        """
        raw = self.meta("role")
        if not raw:
            return None
        try:
            return RoleName(raw.strip().upper())
        except ValueError:
            return None

    def tool_names(self) -> tuple[str, ...]:
        """已声明的工具名元组（保持声明顺序）。"""
        return tuple(spec.name for spec in self.tools)


class Usage(_Dto):
    """token 用量。

    ``cached_tokens`` 是 ``prompt_tokens`` 的**子集**（命中前缀缓存的部分），
    计价时按「非缓存部分走输入价 + 缓存部分走缓存价」拆算，不重复计费。
    """

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        """``prompt + completion``（不含 cached，避免重复计数）。"""
        return self.prompt_tokens + self.completion_tokens

    @property
    def billable_prompt_tokens(self) -> int:
        """按输入价计费的 token 数（扣掉缓存命中部分，且不为负）。"""
        return max(0, self.prompt_tokens - self.cached_tokens)

    def merged(self, other: Usage) -> Usage:
        """与另一份用量相加，返回新对象。

        Args:
            other: 另一份用量。

        Returns:
            相加后的新 :class:`Usage`。
        """
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
        )


class ChatResult(_Dto):
    """一次非流式补全的完整结果。"""

    content: str = ""
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    finish_reason: FinishReason = "stop"
    latency_ms: int = Field(default=0, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        """是否请求了工具调用。"""
        return bool(self.tool_calls)

    @property
    def truncated(self) -> bool:
        """是否因为 ``max_tokens`` 被截断。"""
        return self.finish_reason == "length"


class StreamChunk(_Dto):
    """流式响应的一个增量片段。

    约定：
    * 文本增量放 ``delta``；
    * 工具调用增量放 ``tool_call_delta``（**已按索引聚合完成的整块**，
      各 Provider 内部负责把碎片拼齐后再吐出，上层无需处理粘包）；
    * 最后一个 chunk 带 ``finish_reason``，并尽量带上 ``usage``。
    """

    delta: str = ""
    tool_call_delta: ToolCallRequest | None = None
    finish_reason: FinishReason | None = None
    usage: Usage | None = None
    index: int = Field(default=0, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_final(self) -> bool:
        """是否是终结片段。"""
        return self.finish_reason is not None


class ProviderCapabilities(_Dto):
    """Provider 能力声明 —— 路由器据此过滤候选。"""

    supports_tools: bool = False
    supports_streaming: bool = False
    supports_json_mode: bool = False
    supports_seed: bool = False
    context_window: int = Field(default=8192, ge=1)
    max_output_tokens: int = Field(default=4096, ge=1)
    supports_embedding: bool = False
    supports_vision: bool = False


class ProviderHealth(_Dto):
    """健康检查结果。"""

    name: str
    healthy: bool = False
    latency_ms: int = Field(default=0, ge=0)
    checked_at: datetime | None = None
    detail: str = ""
    model: str | None = None
    error_code: str | None = None


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


class ProviderConfigModel(_Dto):
    """Provider 运行期配置 —— ORM ``provider_configs`` 的**内存投影**。

    刻意不直接用 ORM 实例作为构造入参，原因有三：

    1. 零 DB 场景（CLI ``--mock`` / 单测）必须能凭空造 Provider；
    2. ORM 里存的是 ``api_key_enc`` 密文，Provider 需要的是解密后的明文，
       解密动作属于调用方（WP7 安全层）而非 Provider；
    3. 避免 Provider 层反向依赖 ``loopforge.models``，保持分层单向。

    Attributes:
        options: 自由扩展位。已被约定的键见 ``docs/guides/providers.md``，
            其中 ``transport`` 允许注入 :class:`httpx.AsyncBaseTransport`
            供测试打桩（生产配置里不会出现）。
    """

    name: str
    kind: str = "openai_compatible"
    base_url: str | None = None
    api_key: SecretStr | None = None
    default_model: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    priority: int = Field(default=100, ge=0)
    failover_to: str | None = None

    @classmethod
    def from_orm_config(cls, config: Any, *, api_key: str | None = None) -> ProviderConfigModel:
        """从 ORM ``ProviderConfig`` 行构造（密文由调用方解密后传入）。

        Args:
            config: :class:`loopforge.models.ProviderConfig` 实例。
            api_key: 已解密的明文 Key；``None`` 表示该 Provider 不需要 Key。

        Returns:
            内存配置对象。
        """
        return cls(
            name=str(config.name),
            kind=str(config.kind or "openai_compatible"),
            base_url=config.base_url,
            api_key=SecretStr(api_key) if api_key else None,
            default_model=config.default_model,
            options=dict(config.options or {}),
            enabled=bool(config.enabled),
            priority=int(config.priority),
            failover_to=config.failover_to,
        )

    def secret(self) -> str:
        """返回明文 Key（不存在时返回空串）。**禁止把返回值写入日志。**"""
        return self.api_key.get_secret_value() if self.api_key is not None else ""

    def option(self, key: str, default: Any = None) -> Any:
        """读取 ``options`` 中的某个键。

        Args:
            key: 键名。
            default: 缺省值。

        Returns:
            配置值。
        """
        return self.options.get(key, default)

    def int_option(self, key: str, default: int) -> int:
        """读取整数型 ``options``（解析失败回落默认值）。"""
        try:
            return int(self.options[key])
        except (KeyError, TypeError, ValueError):
            return default

    def float_option(self, key: str, default: float) -> float:
        """读取浮点型 ``options``（解析失败回落默认值）。"""
        try:
            return float(self.options[key])
        except (KeyError, TypeError, ValueError):
            return default

    def bool_option(self, key: str, default: bool) -> bool:
        """读取布尔型 ``options``（解析失败回落默认值）。"""
        raw = self.options.get(key, default)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw)


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


class RoutingHint(_Dto):
    """路由提示 —— 调用方对本次选型的额外要求。"""

    provider: str | None = None
    model: str | None = None
    require_tools: bool = False
    require_streaming: bool = False
    require_json_mode: bool = False
    min_context_window: int = Field(default=0, ge=0)
    max_cost_usd_per_mtok: Decimal | None = None
    exclude: list[str] = Field(default_factory=list)
    prefer_cheapest: bool = False


@dataclass(slots=True)
class ProviderBinding:
    """路由结果 —— 角色到「活的 Provider 实例 + 模型 + 采样参数」的绑定。

    Attributes:
        role: 触发本次解析的角色。
        provider_name: 被选中的 Provider 注册名。
        model: 被选中的模型名。
        temperature: 采样温度。
        max_tokens: 单次输出上限。
        provider: **已实例化**的 Provider 对象。
        fallbacks: 故障转移候选（``(provider_name, model)`` 有序元组）。
        source: 这次绑定来自哪一层配置，便于排障。
    """

    role: RoleName
    provider_name: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 4096
    provider: BaseProvider | None = None
    fallbacks: tuple[tuple[str, str], ...] = ()
    source: Literal["hint", "database", "settings", "default"] = "default"

    def build_request(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        metadata: dict[str, str] | None = None,
        **overrides: Any,
    ) -> ChatRequest:
        """按本绑定的采样参数组装 :class:`ChatRequest`。

        Args:
            messages: 对话消息。
            tools: 工具声明。
            metadata: 追加的元数据（会自动补上 ``role``）。
            **overrides: 覆盖 ``ChatRequest`` 的任意字段。

        Returns:
            组装好的请求。
        """
        meta: dict[str, str] = {"role": self.role.value}
        if metadata:
            meta.update({str(k): str(v) for k, v in metadata.items()})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools or [],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "metadata": meta,
        }
        payload.update(overrides)
        return ChatRequest(**payload)

    def require_provider(self) -> BaseProvider:
        """返回非空的 Provider 实例。

        Returns:
            Provider 实例。

        Raises:
            ValueError: 绑定未携带实例（仅在手工构造的裸绑定上会发生）。
        """
        if self.provider is None:
            raise ValueError(f"ProviderBinding({self.provider_name}) 未携带 Provider 实例")
        return self.provider


# ---------------------------------------------------------------------------
# 向量化
# ---------------------------------------------------------------------------


class EmbeddingRequest(_Dto):
    """文本向量化请求。"""

    model: str
    inputs: list[str] = Field(default_factory=list)
    dimensions: int | None = Field(default=None, ge=1)
    timeout_s: float = Field(default=60.0, gt=0.0)
    metadata: dict[str, str] = Field(default_factory=dict)


class EmbeddingResult(_Dto):
    """文本向量化结果。"""

    vectors: list[list[float]] = Field(default_factory=list)
    model: str = ""
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = Field(default=0, ge=0)

    @property
    def dimension(self) -> int:
        """向量维度（空结果返回 0）。"""
        return len(self.vectors[0]) if self.vectors else 0


# ---------------------------------------------------------------------------
# 故障注入
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FaultSpec:
    """一条故障注入规则（MockProvider 与 WP4 恢复测试用）。

    Attributes:
        kind: 故障种类。
        at_call: 从第几次调用开始触发（1-based）。
        times: 连续触发多少次；``-1`` 表示一直触发。
        model: 仅对该模型生效；``None`` 表示所有模型。
        message: 自定义错误消息。
    """

    kind: FaultKind
    at_call: int = 1
    times: int = 1
    model: str | None = None
    message: str = ""
    _fired: int = field(default=0, repr=False)

    def matches(self, *, call_index: int, model: str) -> bool:
        """判断当前调用是否应触发本故障。

        Args:
            call_index: 当前是第几次调用（1-based）。
            model: 当前模型名。

        Returns:
            应触发返回 ``True``。
        """
        if self.model is not None and self.model != model:
            return False
        if call_index < self.at_call:
            return False
        if self.times >= 0 and self._fired >= self.times:
            return False
        return True

    def fire(self) -> None:
        """记一次已触发。"""
        self._fired += 1

    def reset(self) -> None:
        """清空触发计数。"""
        self._fired = 0
