# ADR-0010：Provider 抽象层

- **状态**：已接受（Accepted）
- **日期**：W1 波次 · WP2 交付
- **决策者**：WP2 Provider 接入 owner
- **影响范围**：WP4 编排、WP5 治理、WP6 角色层

---

## 背景

LoopForge 需要「模型无关」：同一套编排逻辑可以跑在 DashScope Qwen、
OpenAI 兼容网关、Anthropic Claude、DeepSeek、Ollama 本地模型上，甚至
零 Key 的 Mock 上。如果不做抽象，每个上游方言都会泄漏进编排引擎，
导致「换模型 = 改代码」。

## 决策

### 1. 单一抽象基类

`loopforge/providers/base.py` 定义 `BaseProvider`，契约冻结于
`TASK_BREAKDOWN.md` §11.3（`chat` / `chat_stream` / `count_tokens` /
`capabilities` / `health_check` / `close`）。

### 2. DTO 与方言解耦

`loopforge/providers/types.py` 定义统一 DTO（`Message` / `ChatRequest` /
`ChatResult` / `StreamChunk` / `Usage` / `ProviderCapabilities`），
字段名逐字冻结、`extra="forbid"`。各 Provider 只在**自己的模块内**做
方言 ↔ DTO 转换，方言差异不出 `providers/` 包。

### 3. 成本算两遍

`estimate_cost()`（调用前预检）+ `actual_cost()`（调用后回填）双出口，
统一走 `pricing.py` 的价目表，金额一律 `Decimal`。缺任何一个，
WP5 的预算三级动作（warn / throttle / halt）就会失效。

### 4. Provider 层不做重试

重试与熔断统一由 `core.resilience` 与 `providers/retry.py`、
`providers/router.py::FailoverChain` 编排，避免双层重试放大成本。

### 5. 注册表自动发现

`providers/registry.py` 用 `Registry[type[BaseProvider]]` 按
`name` / `kind` / `aliases` 三键注册。新增 Provider = 新增 1 个文件 +
注册表一行，**不改核心**（§13.3）。

## 备选方案

| 方案 | 否决原因 |
|---|---|
| 每个上游一个独立接口 | 编排层要写 N 套分支，无法模型无关 |
| 用协议类（Protocol）而非 ABC | 缺少强制实现检查，遗漏方法到运行时才炸 |
| 把重试放进 Provider | 双层重试放大成本，且 Provider 无法感知全局预算 |
| 直接用各家 SDK（openai / anthropic 官方包） | 多一层依赖与版本锁；`httpx.AsyncClient` 已足够且可统一打桩 |

## 影响

- 上游新增 Provider：按 `docs/guides/writing-providers.md` 三步接入；
- 下游调用：见 `docs/guides/providers.md` 的「标准调用姿势」；
- 测试：真实 Provider 一律用 `httpx.MockTransport` 打桩，测试不联网。
