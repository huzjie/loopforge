"""Provider 与 Agent 角色 DTO —— 模型无关抽象的配置面。

**安全铁律**：``api_key`` 只在写入方向出现，任何读模型都不得包含密钥或密文。
:class:`ProviderConfigRead` 只暴露 :attr:`ProviderConfigRead.has_api_key` 布尔位，
让运维知道「配没配」，但拿不到「配的是什么」。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from loopforge.core.enums import RoleName
from loopforge.schemas.common import LFModel, ORMModel

__all__ = [
    "PROVIDER_KINDS",
    "ProviderConfigCreate",
    "ProviderConfigUpdate",
    "ProviderConfigRead",
    "ProviderTestRequest",
    "ProviderTestResult",
    "ModelPricing",
    "AgentRoleCreate",
    "AgentRoleUpdate",
    "AgentRoleRead",
    "FailoverChain",
]

#: 支持的 Provider 类型（OpenAI 兼容协议覆盖绝大多数自托管网关）。
PROVIDER_KINDS: tuple[str, ...] = (
    "openai",
    "anthropic",
    "qwen",
    "deepseek",
    "ollama",
    "vllm",
    "openai_compatible",
    "mock",
)

_ZERO = Decimal("0")


class ProviderConfigCreate(LFModel):
    """新增或覆盖一个 Provider 配置。"""

    name: str = Field(min_length=1, max_length=64, description="Provider 唯一名")
    kind: str = Field(default="openai_compatible", max_length=32, description="Provider 类型")
    base_url: str = Field(default="", max_length=500, description="接口地址")
    api_key: str | None = Field(
        default=None, max_length=500, description="密钥明文；省略表示保留原值"
    )
    models: list[str] = Field(default_factory=list, description="可用模型列表")
    default_model: str | None = Field(default=None, max_length=200, description="默认模型")
    priority: int = Field(default=100, ge=0, le=10_000, description="故障转移优先级，越小越先")
    enabled: bool = Field(default=True, description="是否启用")
    timeout_seconds: float = Field(default=120.0, gt=0.0, le=3_600.0, description="请求超时")
    max_retries: int = Field(default=2, ge=0, le=10, description="最大重试次数")
    extra: dict[str, object] = Field(default_factory=dict, description="额外参数")

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        """名字统一小写去空白，避免 ``OpenAI`` / ``openai`` 建成两条。"""
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("name 不能为空")
        return normalized

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        """类型必须在白名单内。"""
        normalized = value.strip().lower()
        if normalized not in PROVIDER_KINDS:
            raise ValueError(f"kind 必须是 {PROVIDER_KINDS} 之一，收到 {value!r}")
        return normalized

    @field_validator("models")
    @classmethod
    def _dedupe_models(cls, value: list[str]) -> list[str]:
        """模型列表去重并剔除空串。"""
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def _check_default_model(self) -> ProviderConfigCreate:
        """默认模型必须在可用列表里；本地 Provider 允许空 base_url。"""
        if self.default_model is not None and self.models and self.default_model not in self.models:
            raise ValueError(f"default_model={self.default_model!r} 不在 models 列表中")
        if self.kind not in ("ollama", "vllm", "mock") and not self.base_url.strip():
            raise ValueError(f"kind={self.kind} 必须提供 base_url")
        return self


class ProviderConfigUpdate(LFModel):
    """局部更新 Provider 配置。"""

    base_url: str | None = Field(default=None, max_length=500, description="接口地址")
    api_key: str | None = Field(default=None, max_length=500, description="新密钥")
    models: list[str] | None = Field(default=None, description="模型列表")
    default_model: str | None = Field(default=None, max_length=200, description="默认模型")
    priority: int | None = Field(default=None, ge=0, le=10_000, description="优先级")
    enabled: bool | None = Field(default=None, description="启停")
    timeout_seconds: float | None = Field(default=None, gt=0.0, le=3_600.0, description="超时")
    max_retries: int | None = Field(default=None, ge=0, le=10, description="重试次数")
    extra: dict[str, object] | None = Field(default=None, description="额外参数")

    def changed_fields(self) -> dict[str, object]:
        """返回本次实际提交的字段（不含 ``api_key``，密钥走单独通道加密）。"""
        payload = self.model_dump(exclude_none=True)
        payload.pop("api_key", None)
        return payload


class ProviderConfigRead(ORMModel):
    """Provider 配置读模型 —— **绝不含密钥**。"""

    id: str = Field(description="配置 ID")
    name: str = Field(description="Provider 名")
    kind: str = Field(default="openai_compatible", description="类型")
    base_url: str = Field(default="", description="接口地址")
    has_api_key: bool = Field(default=False, description="是否已配置密钥")
    models: list[str] = Field(default_factory=list, description="可用模型")
    default_model: str | None = Field(default=None, description="默认模型")
    priority: int = Field(default=100, description="优先级")
    enabled: bool = Field(default=True, description="是否启用")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")

    @property
    def is_local(self) -> bool:
        """是否为本地部署的推理服务。"""
        return self.kind in ("ollama", "vllm")

    @property
    def is_usable(self) -> bool:
        """是否具备被调度的最低条件。"""
        if not self.enabled:
            return False
        if not self.models:
            return False
        return self.is_local or self.has_api_key


class ProviderTestRequest(LFModel):
    """连通性探测请求。"""

    name: str = Field(min_length=1, max_length=64, description="Provider 名")
    model: str | None = Field(default=None, max_length=200, description="指定模型，省略用默认")
    prompt: str = Field(default="ping", max_length=2_000, description="探测提示词")
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0, description="探测超时")


class ProviderTestResult(LFModel):
    """连通性探测结果。"""

    name: str = Field(description="Provider 名")
    ok: bool = Field(description="是否连通")
    model: str | None = Field(default=None, description="实际使用的模型")
    latency_ms: int = Field(default=0, ge=0, description="往返耗时")
    error_code: str | None = Field(default=None, description="失败错误码")
    message: str = Field(default="", description="补充说明")
    tested_at: datetime | None = Field(default=None, description="探测时间")


class ModelPricing(LFModel):
    """单模型定价（USD / 1K tokens）。"""

    provider: str = Field(min_length=1, max_length=64, description="Provider 名")
    model: str = Field(min_length=1, max_length=200, description="模型名")
    prompt_per_1k: Decimal = Field(default=_ZERO, ge=_ZERO, description="输入单价")
    completion_per_1k: Decimal = Field(default=_ZERO, ge=_ZERO, description="输出单价")
    currency: str = Field(default="USD", max_length=8, description="币种")

    def estimate(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        """按 token 数估算成本。"""
        prompt_cost = self.prompt_per_1k * Decimal(max(0, prompt_tokens)) / Decimal(1000)
        completion_cost = self.completion_per_1k * Decimal(max(0, completion_tokens)) / Decimal(1000)
        return prompt_cost + completion_cost


class AgentRoleCreate(LFModel):
    """定义一个 Agent 角色的模型与工具绑定。"""

    name: RoleName = Field(description="角色名")
    provider: str = Field(min_length=1, max_length=64, description="绑定的 Provider")
    model: str = Field(min_length=1, max_length=200, description="绑定的模型")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0, description="采样温度")
    max_tokens: int = Field(default=4_096, ge=1, le=1_000_000, description="最大输出 token")
    system_prompt: str = Field(default="", max_length=100_000, description="系统提示词")
    tool_allowlist: dict[str, object] = Field(default_factory=dict, description="工具白名单")
    enabled: bool = Field(default=True, description="是否启用")

    @model_validator(mode="after")
    def _check_temperature_for_reviewer(self) -> AgentRoleCreate:
        """评审与测试角色必须低温，随机性会让评审结论不可复现。"""
        if self.name in (RoleName.REVIEWER, RoleName.TESTER) and self.temperature > 0.5:
            raise ValueError(f"{self.name} 角色的 temperature 不得超过 0.5（当前 {self.temperature}）")
        return self


class AgentRoleUpdate(LFModel):
    """局部更新角色配置。"""

    provider: str | None = Field(default=None, max_length=64, description="Provider")
    model: str | None = Field(default=None, max_length=200, description="模型")
    temperature: float | None = Field(default=None, ge=0.0, le=2.0, description="温度")
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000, description="最大输出")
    system_prompt: str | None = Field(default=None, max_length=100_000, description="系统提示词")
    tool_allowlist: dict[str, object] | None = Field(default=None, description="工具白名单")
    enabled: bool | None = Field(default=None, description="启停")

    def changed_fields(self) -> dict[str, object]:
        """返回本次实际提交的字段。"""
        return self.model_dump(exclude_none=True)


class AgentRoleRead(ORMModel):
    """角色配置读模型。"""

    id: str = Field(description="角色配置 ID")
    name: RoleName = Field(description="角色名")
    provider: str = Field(description="Provider")
    model: str = Field(description="模型")
    temperature: float = Field(default=0.2, description="温度")
    max_tokens: int = Field(default=4_096, description="最大输出 token")
    system_prompt: str = Field(default="", description="系统提示词")
    tool_allowlist: dict[str, object] = Field(default_factory=dict, description="工具白名单")
    enabled: bool = Field(default=True, description="是否启用")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")

    @property
    def model_ref(self) -> str:
        """``provider/model`` 形式的完整引用。"""
        return f"{self.provider}/{self.model}"


class FailoverChain(LFModel):
    """故障转移链 —— 主 Provider 挂掉后的候补顺序。"""

    role: RoleName = Field(description="适用角色")
    entries: list[str] = Field(default_factory=list, description="``provider/model`` 有序列表")

    @model_validator(mode="after")
    def _check_entries(self) -> FailoverChain:
        """链路去重、非空、格式校验。"""
        if not self.entries:
            raise ValueError("故障转移链不能为空")
        deduped: list[str] = []
        for entry in self.entries:
            normalized = entry.strip()
            if "/" not in normalized:
                raise ValueError(f"链路项必须是 provider/model 形式，收到 {entry!r}")
            if normalized not in deduped:
                deduped.append(normalized)
        object.__setattr__(self, "entries", deduped)
        return self

    @property
    def primary(self) -> str:
        """首选项。"""
        return self.entries[0]

    def next_after(self, current: str) -> str | None:
        """返回 ``current`` 之后的下一个候补；已是最后一个则返回 ``None``。"""
        try:
            index = self.entries.index(current)
        except ValueError:
            return self.entries[0]
        if index + 1 >= len(self.entries):
            return None
        return self.entries[index + 1]
