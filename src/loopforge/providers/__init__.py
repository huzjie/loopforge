"""``loopforge.providers`` —— 模型供应商接入层（WP2）。

模块地图::

    base.py             BaseProvider / HttpProviderBase（冻结契约 §11.3）
    types.py            全部 DTO（Message / ChatRequest / ChatResult / ...）
    token_counter.py    token 估算（tiktoken 可选 + 启发式降级）
    pricing.py          价目表与成本换算（estimate/actual 两遍）
    cost.py             成本计量与落账（llm_calls + cost_records）
    streaming.py        SSE / NDJSON 流式解析与工具调用聚合
    mock.py             MockProvider（零 Key 生命线）
    openai_compat.py    OpenAI 兼容协议
    dashscope.py        阿里百炼 DashScope（qwen3.8 系）
    anthropic.py        Anthropic Messages API
    deepseek.py         DeepSeek（含 reasoning_content）
    ollama.py           Ollama 本地推理
    retry.py            重试与退避（可重试 / 不可重试分类）
    router.py           ProviderRouter + FailoverChain
    registry.py         Provider 注册与实例化

下游（WP4 / WP5 / WP6）标准用法
-------------------------------
1. 零 Key 场景::

       from loopforge.providers import create_mock_provider
       provider = create_mock_provider()
       result = await provider.chat(request)

2. 按角色路由（推荐，配置驱动）::

       from loopforge.providers import ProviderRouter
       router = ProviderRouter()
       binding = await router.resolve(RoleName.CODER)
       result = await binding.require_provider().chat(
           binding.build_request(messages)
       )

3. 带故障转移与重试::

       from loopforge.providers import FailoverChain, call_with_retry
       result = await FailoverChain(router.breakers).run(
           binding, lambda p, m: p.chat(binding.build_request(messages))
       )
"""

from __future__ import annotations

from loopforge.providers.base import (
    DEFAULT_USER_AGENT,
    BaseProvider,
    HttpProviderBase,
    ProviderBadResponseError,
    ProviderContentFilteredError,
    ProviderModelUnsupportedError,
    map_http_status,
    map_transport_error,
)
from loopforge.providers.cost import (
    CostAggregate,
    CostEntry,
    CostMeter,
    estimate_request_cost,
    summarize_request,
    summarize_result,
)
from loopforge.providers.dashscope import (
    DASHSCOPE_COMPAT_BASE_URL,
    DASHSCOPE_DEFAULT_BASE_URL,
    DashScopeProvider,
)
from loopforge.providers.deepseek import (
    DEFAULT_DEEPSEEK_BASE_URL,
    REASONING_FIELD,
    DeepSeekProvider,
)
from loopforge.providers.mock import (
    DEFAULT_MOCK_MODEL,
    FUNCTION_TEMPLATES,
    MockCodegen,
    MockCodegenResult,
    MockProvider,
    extract_function_name,
    pick_template,
)
from loopforge.providers.ollama import (
    DEFAULT_OLLAMA_BASE_URL,
    OllamaProvider,
)
from loopforge.providers.openai_compat import (
    DEFAULT_OPENAI_BASE_URL,
    OpenAICompatProvider,
    build_openai_messages,
    build_openai_tools,
    parse_chat_payload,
    parse_usage,
)
from loopforge.providers.pricing import (
    MONEY_QUANT,
    ModelNotPricedError,
    ModelPrice,
    PricingTable,
    TOKENS_PER_MILLION,
    actual_cost,
    estimate_cost,
    get_pricing_table,
    load_pricing,
    price_of,
    quantize_money,
    reload_pricing,
    set_pricing_table,
)
from loopforge.providers.registry import (
    autoload_providers,
    create_mock_provider,
    create_provider,
    get_provider,
    get_provider_class,
    load_builtin_providers,
    provider_registry,
)
from loopforge.providers.retry import (
    NON_RETRYABLE_PROVIDER_ERRORS,
    RETRYABLE_PROVIDER_ERRORS,
    ProviderRetryContext,
    call_with_retry,
    provider_retry_policy,
    should_retry_provider_error,
)
from loopforge.providers.router import (
    DEFAULT_FAILOVER_CHAIN,
    FailoverChain,
    ProviderRouter,
    SwitchRecord,
)
from loopforge.providers.streaming import (
    SSE_DONE,
    SSEEvent,
    ToolCallAccumulator,
    aiter_ndjson,
    aiter_sse,
    collect_stream,
    parse_sse_block,
)
from loopforge.providers.token_counter import (
    TIKTOKEN_AVAILABLE,
    count_messages,
    count_text,
    count_tools,
)
from loopforge.providers.types import (
    ChatRequest,
    ChatResult,
    EmbeddingRequest,
    EmbeddingResult,
    FaultKind,
    FaultSpec,
    FinishReason,
    Message,
    MessageRole,
    ProviderBinding,
    ProviderCapabilities,
    ProviderConfigModel,
    ProviderHealth,
    RoutingHint,
    StreamChunk,
    ToolCallRequest,
    ToolChoice,
    ToolSpec,
    Usage,
)

#: 初始化内置 Provider 注册表（幂等，导入即注册）。
load_builtin_providers()

__all__ = [
    # base
    "DEFAULT_USER_AGENT",
    "BaseProvider",
    "HttpProviderBase",
    "ProviderBadResponseError",
    "ProviderContentFilteredError",
    "ProviderModelUnsupportedError",
    "map_http_status",
    "map_transport_error",
    # cost
    "CostAggregate",
    "CostEntry",
    "CostMeter",
    "estimate_request_cost",
    "summarize_request",
    "summarize_result",
    # dashscope
    "DASHSCOPE_COMPAT_BASE_URL",
    "DASHSCOPE_DEFAULT_BASE_URL",
    "DashScopeProvider",
    # deepseek
    "DEFAULT_DEEPSEEK_BASE_URL",
    "REASONING_FIELD",
    "DeepSeekProvider",
    # mock
    "DEFAULT_MOCK_MODEL",
    "FUNCTION_TEMPLATES",
    "MockCodegen",
    "MockCodegenResult",
    "MockProvider",
    "extract_function_name",
    "pick_template",
    # ollama
    "DEFAULT_OLLAMA_BASE_URL",
    "OllamaProvider",
    # openai_compat
    "DEFAULT_OPENAI_BASE_URL",
    "OpenAICompatProvider",
    "build_openai_messages",
    "build_openai_tools",
    "parse_chat_payload",
    "parse_usage",
    # pricing
    "MONEY_QUANT",
    "ModelNotPricedError",
    "ModelPrice",
    "PricingTable",
    "TOKENS_PER_MILLION",
    "actual_cost",
    "estimate_cost",
    "get_pricing_table",
    "load_pricing",
    "price_of",
    "quantize_money",
    "reload_pricing",
    "set_pricing_table",
    # registry
    "autoload_providers",
    "create_mock_provider",
    "create_provider",
    "get_provider",
    "get_provider_class",
    "load_builtin_providers",
    "provider_registry",
    # retry
    "NON_RETRYABLE_PROVIDER_ERRORS",
    "RETRYABLE_PROVIDER_ERRORS",
    "ProviderRetryContext",
    "call_with_retry",
    "provider_retry_policy",
    "should_retry_provider_error",
    # router
    "DEFAULT_FAILOVER_CHAIN",
    "FailoverChain",
    "ProviderRouter",
    "SwitchRecord",
    # streaming
    "SSE_DONE",
    "SSEEvent",
    "ToolCallAccumulator",
    "aiter_ndjson",
    "aiter_sse",
    "collect_stream",
    "parse_sse_block",
    # token_counter
    "TIKTOKEN_AVAILABLE",
    "count_messages",
    "count_text",
    "count_tools",
    # types
    "ChatRequest",
    "ChatResult",
    "EmbeddingRequest",
    "EmbeddingResult",
    "FaultKind",
    "FaultSpec",
    "FinishReason",
    "Message",
    "MessageRole",
    "ProviderBinding",
    "ProviderCapabilities",
    "ProviderConfigModel",
    "ProviderHealth",
    "RoutingHint",
    "StreamChunk",
    "ToolCallRequest",
    "ToolChoice",
    "ToolSpec",
    "Usage",
]
