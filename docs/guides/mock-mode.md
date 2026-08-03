# Mock 模式（零 Key 端到端）

- **适用对象**：开发者本地调试、CI、WP4 恢复测试
- **状态**：WP2 交付（W1）

---

## 1. 是什么

`MockProvider` 是一个**完全离线、确定性**的伪 LLM：

- 不需要任何 API Key / 网络；
- 根据 prompt 里的任务描述生成**真实可运行的 Python 代码**（含 unified diff）；
- 返回结构化 `tool_call`、支持流式与多轮对话；
- 相同入参 → 字节级相同输出；
- 可注入五类故障，供恢复逻辑测试。

## 2. 启用方式

CLI / 默认配置 `config/default.yaml` 已把 `providers.default` 设为 `mock`，
因此**开箱即用**。也可以显式构造：

```python
from loopforge.providers import create_mock_provider
provider = create_mock_provider()  # model=mock-large
```

带故障注入：

```python
provider = create_mock_provider(options={
    "faults": [
        {"kind": "rate_limit", "at_call": 1, "times": 2},
        {"kind": "server_error", "at_call": 3, "times": 1},
    ],
})
```

## 3. 确定性契约

输出由种子派生：

```
seed = sha256(role | loop_index | task_key | context_digest
              | model | messages_digest | tools_digest)[:16]
```

- `role` / `loop_index` / `task_key` / `context_digest` 来自
  `ChatRequest.metadata`；
- 相同入参（含 metadata 与消息序列）→ 相同输出；
- 不同 `loop_index` → 不同输出（模拟多轮探索）；
- 实现禁止使用未播种的 `random` / 未排序 dict 迭代 / 系统时间。

测试断言示例：

```python
first = await provider.chat(req)
for _ in range(100):
    assert (await provider.chat(req)).content == first.content
```

## 4. 能力矩阵

| 能力 | 支持 | 说明 |
|---|---|---|
| 非流式 `chat` | ✅ | 文本 / 代码 / JSON 三种风格 |
| 流式 `chat_stream` | ✅ | 按 `stream_chunk_size` 分片 |
| 工具调用 | ✅ | `required` 强制；`auto` 按文本提到工具名 |
| 多轮对话 | ✅ | 回复引用前文轮数 |
| `count_tokens` | ✅ | 与全局估算器一致 |
| `health_check` | ✅ | 恒健康（除非注入 auth 故障） |
| embedding | ✅ | 确定性 8 维向量 |
| 代码生成 | ✅ | 内置 10+ 模板，`compile()` 可通过 |

## 5. 故障注入

`options["faults"]` 是规则列表，每项：

| 键 | 类型 | 说明 |
|---|---|---|
| `kind` | str | `auth` / `rate_limit` / `timeout` / `server_error` / `truncate` / `content_filter` / `bad_response` |
| `at_call` | int | 从第几次调用开始触发（1-based） |
| `times` | int | 连续触发次数；`-1` 表示一直触发 |
| `model` | str | 仅对该模型生效 |
| `message` | str | 自定义错误消息 |

`truncate` 比较特殊：不抛异常，而是把响应截断并把 `finish_reason` 置为
`length`，用于测试「输出被 max_tokens 截断」的恢复路径。

## 6. 对下游的意义

- **WP4 恢复测试**：用 `faults` 注入超时 / 限流 / 5xx，验证
  `call_with_retry` 退避重试与 `FailoverChain` 切换；
- **零 Key 端到端**：`examples/` 的示例可直接用 mock 跑通，产出可执行代码；
- **预算演练**：mock 价目刻意贴近真实中端模型，`warn / throttle / halt`
  三级动作在 mock 下也能真实触发。

## 7. 注意

- Mock 不模拟真实模型的推理质量，**不要**用它评估生成代码的正确性上限；
- 生产环境请通过 `config/default.yaml` 把 `providers.default` 换成真实
  Provider（DashScope / OpenAI / Anthropic / DeepSeek / Ollama）。
