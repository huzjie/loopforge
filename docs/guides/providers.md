# Provider 接入指南

- **适用对象**：WP4 编排引擎、WP5 治理层、WP6 角色层
- **状态**：WP2 交付（W1）
- **冻结依据**：`docs/TASK_BREAKDOWN.md` §4 / §11.3

---

## 1. 一句话

让上层「换模型像换配置一样简单」，并交付一个**零 Key 也能产出可运行代码**的
MockProvider。

## 2. 支持的 Provider

| 注册名 | 类 | kind | 默认端点 | 默认模型 | 成本 |
|---|---|---|---|---|---|
| `mock` | `MockProvider` | `mock` | 无（离线） | `mock-large` | 0 |
| `openai` | `OpenAICompatProvider` | `openai_compatible` | `https://api.openai.com/v1` | `gpt-4o-mini` | 按价目 |
| `dashscope` | `DashScopeProvider` | `dashscope` | `https://dashscope.aliyuncs.com/api/v1` | `qwen3.8-max` | 按价目 |
| `anthropic` | `AnthropicProvider` | `anthropic` | `https://api.anthropic.com` | `claude-sonnet-4-5` | 按价目 |
| `deepseek` | `DeepSeekProvider` | `deepseek` | `https://api.deepseek.com` | `deepseek-chat` | 按价目 |
| `ollama` | `OllamaProvider` | `ollama` | `http://localhost:11434` | `qwen3.8-27b` | 0（本地） |

DashScope 的 OpenAI 兼容通道：把 `base_url` 配成
`https://dashscope.aliyuncs.com/compatible-mode/v1` 即可，代码零改动。

## 3. 标准调用姿势

### 3.1 零 Key（默认 / CLI `--mock`）

```python
from loopforge.providers import create_mock_provider
from loopforge.providers.types import ChatRequest, Message

provider = create_mock_provider()
result = await provider.chat(
    ChatRequest(
        model="mock-large",
        messages=[Message.user("请写一个 def factorial(n) 函数")],
        metadata={"role": "CODER", "loop_index": "0", "task_key": "t1"},
    )
)
print(result.content)   # 可运行的 Python 代码
```

### 3.2 按角色路由（推荐）

```python
from loopforge.providers import ProviderRouter
from loopforge.core.enums import RoleName

router = ProviderRouter()
binding = await router.resolve(RoleName.CODER)
# binding: provider_name / model / temperature / max_tokens / fallbacks
result = await binding.require_provider().chat(
    binding.build_request(messages, metadata={"run_id": run_id})
)
```

路由优先级：hint 显式 > DB `agent_roles` > `settings.roles` > 默认。

### 3.3 带故障转移与重试

```python
from loopforge.providers import FailoverChain, call_with_retry, ProviderRouter

router = ProviderRouter()
binding = await router.resolve(RoleName.CODER)

# 重试（限流 / 超时 / 服务端错误自动退避）
result = await call_with_retry(
    lambda: binding.require_provider().chat(request),
    provider=binding.provider_name,
    model=binding.model,
)

# 故障转移（沿 fallbacks 链切换）
chain = FailoverChain(router.breakers)
result = await chain.run(binding, lambda p, m: p.chat(request))
```

### 3.4 按配置实例化真实 Provider

```python
from loopforge.providers import create_provider
from loopforge.providers.types import ProviderConfigModel

provider = create_provider(
    ProviderConfigModel(
        name="dashscope",
        kind="dashscope",
        base_url="https://dashscope.aliyuncs.com/api/v1",
        api_key=SecretStr(os.environ["DASHSCOPE_API_KEY"]),
        default_model="qwen3.8-max",
        options={"request_timeout_s": 60},
    )
)
```

## 4. 配置项（`ProviderConfigModel.options`）

| 键 | 类型 | 适用 | 说明 |
|---|---|---|---|
| `request_timeout_s` | float | 全部 | 请求超时秒数，默认 120 |
| `headers` | dict | HTTP Provider | 追加请求头 |
| `proxy` | str | HTTP Provider | 代理地址 |
| `transport` | httpx transport | 测试 | 注入 `httpx.MockTransport` 打桩 |
| `faults` | list[dict] | mock | 故障注入规则（见 mock-mode.md） |
| `response_style` | str | mock | `text` / `code` / `auto` |
| `stream_chunk_size` | int | mock | 流式分片大小 |
| `embedding_path` | str | openai | embedding 端点路径 |

## 5. 成本：永远算两遍

```python
from loopforge.providers import estimate_cost, actual_cost
from loopforge.providers.types import Usage

# 调用前（WP5 BudgetGuard 预检）
estimate = estimate_cost("openai", "gpt-4o",
                         prompt_tokens=1000, completion_tokens=500)

# 调用后（据真实 usage 回填）
usage = Usage(prompt_tokens=1000, completion_tokens=500)
actual = actual_cost("openai", "gpt-4o", usage)
```

金额一律 `Decimal`，量化到 8 位小数；缓存 token 按 `cached_input` 价拆算。

## 6. 错误码

所有 Provider 异常统一归一为 `LF-PROVIDER-07xx`：

| 码 | 含义 | retryable |
|---|---|---|
| `0701` | 鉴权失败 | 否 |
| `0702` | 限流 | 是 |
| `0703` | 超时 | 是 |
| `0704` | 服务端错误 | 是 |
| `0705` | 全部 Provider 不可用 | 是 |
| `0706` | 模型不支持 | 否 |
| `0707` | Provider 未注册 | 否 |
| `0708` | 响应解析失败 | 是 |
| `0709` | 内容被拦截 | 否 |

上层只认错误码，不认各家原始报文。
