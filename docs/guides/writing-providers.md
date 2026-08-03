# 如何新增一个 Provider

- **适用对象**：LoopForge 贡献者、自建网关接入者
- **状态**：WP2 交付（W1）
- **约定**：`TASK_BREAKDOWN.md` §13.3（新增实现 = 新增 1 个文件 + 注册，不改核心）

---

## 1. 最小实现

新增 Provider 只需三步：

1. 新建一个模块，例如 `src/loopforge/providers/my_provider.py`；
2. 实现 `BaseProvider` 的五个抽象成员；
3. 在 `providers/registry.py` 的 `_BUILTIN_MODULES` 与 `_register_builtin_classes`
   中加入你的模块与类名。

### 示例骨架

```python
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, ClassVar

from loopforge.providers.base import BaseProvider
from loopforge.providers.types import (
    ChatRequest, ChatResult, Message,
    ProviderCapabilities, ProviderHealth, StreamChunk,
)

class MyProvider(BaseProvider):
    name: ClassVar[str] = "my_provider"
    aliases: ClassVar[tuple[str, ...]] = ("my",)
    kind: ClassVar[str] = "my_provider"
    default_base_url: ClassVar[str] = "https://example.com/v1"
    default_model: ClassVar[str] = "my-model"
    free: ClassVar[bool] = False

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_json_mode=True,
            supports_seed=False,
            context_window=128_000,
            max_output_tokens=8_192,
        )

    async def chat(self, request: ChatRequest) -> ChatResult:
        # 发请求（优先用 HttpProviderBase.request_json 做异常归一）
        ...

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        ...

    def count_tokens(self, messages: Sequence[Message], model: str) -> int:
        from loopforge.providers.token_counter import count_messages
        return count_messages(messages, model)

    async def health_check(self) -> ProviderHealth:
        return await self.probe("/health", expect=200)
```

## 2. 推荐：继承 `HttpProviderBase`

如果对方是 REST + JSON Provider，直接继承 `HttpProviderBase`，免费获得：

- 连接池生命周期（`client()` / `close()`）；
- 超时与重试兜底；
- 状态码归一（`map_http_status`）；
- 传输层异常归一（`map_transport_error`）；
- 探活骨架（`probe()`）。

```python
from loopforge.providers.base import HttpProviderBase

class MyProvider(HttpProviderBase):
    ...
```

## 3. 硬性约束

1. **异常必须归一**：任何失败都抛 `LF-PROVIDER-07xx` 子类，禁止裸抛
   `httpx.HTTPError` / `ValueError`；
2. **Provider 层不做重试**：重试由 `core.resilience.retry_async` 与
   `FailoverChain` 编排；
3. **时间只走 Clock**：禁止 `datetime.now()`，用 `self.clock`；
4. **金额 Decimal**：成本换算只调 `pricing.estimate_cost/actual_cost`；
5. **不落明文 Key**：`repr()` 与日志不得包含 `api_key`；
6. **测试不得联网**：用 `httpx.MockTransport` 打桩。

## 4. 测试要求

在 `tests/unit/providers/` 新增 `test_my_provider.py`，至少覆盖：

- 非流式 chat（MockTransport 打桩）；
- 流式 chat_stream；
- `count_tokens`；
- `health_check`；
- 错误归一（401 / 429 / 500 → 对应错误码）。

复用 `tests/fixtures/providers.py` 里的 `mock_provider` 等夹具做对照。

## 5. 注册

在 `providers/registry.py`：

```python
_BUILTIN_MODULES = (
    ...
    "loopforge.providers.my_provider",
)

_register_builtin_classes 的 mapping 里加入：
    ("loopforge.providers.my_provider", "MyProvider"),
```

然后：

```python
from loopforge.providers import get_provider_class
assert get_provider_class("my_provider") is MyProvider
```

## 6. 价目

在 `config/pricing.yaml` 的 `providers:` 下新增一节，至少给一个 `"*"` 通配，
否则 `estimate_cost/actual_cost` 会抛 `ModelNotPricedError`：

```yaml
my_provider:
  my-model:
    input: 0.50
    output: 1.50
  "*":
    input: 0.50
    output: 1.50
```
