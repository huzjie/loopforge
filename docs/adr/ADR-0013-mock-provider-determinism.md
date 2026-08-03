# ADR-0013：MockProvider 确定性设计

- **状态**：已接受（Accepted）
- **日期**：W1 波次 · WP2 交付
- **决策者**：WP2 Provider 接入 owner
- **影响范围**：WP4 恢复测试、零 Key 端到端演练、CI

---

## 背景

LoopForge 的验收标准之一（AC-A2）是「零 Key 端到端跑通」：没有 API Key
也能把整个编排流程（计划 → 编码 → 评审 → 测试 → 总结）跑起来，并产出
**可运行**的代码。同时 WP4 需要可复现的故障注入来测试恢复逻辑。

如果 MockProvider 的输出每次都不一样，那么：
- 端到端演练无法复现 bug；
- 恢复测试无法断言「故障前 vs 故障后」；
- CI 里同一用例不同结果，无法定位回归。

因此 MockProvider 的确定性是**硬指标**。

## 决策

### 1. 种子派生公式

```
seed = int(sha256(
    role | loop_index | task_key | context_digest
    | model | messages_digest | tools_digest
)[:16], 16)
```

- `role` / `loop_index` / `task_key` / `context_digest` 取自
  `ChatRequest.metadata`；
- `messages_digest` / `tools_digest` 用 `core.hashing.canonical_json`
  （键排序、无空格）序列化后取 sha256；
- 相同入参 → 相同种子 → 字节级相同输出。

### 2. 所有随机分支必须播种

`MockProvider` 内部所有随机分支（模板并列选择、参数抽样等）一律使用
`random.Random(seed)`，**禁止**：
- 未播种的 `random` / `random.random()`；
- 未排序的 `dict` 迭代（键序依赖哈希盐）；
- 系统时间（`datetime.now()` / `time.time()`）。

### 3. 代码生成也是确定性的

`MockCodegen` 内置 10+ 真实模板（阶乘 / 斐波那契 / 回文 / 二分 / FizzBuzz …），
按任务描述关键词匹配，匹配并列时用播种的 `rng` 定选。产出代码
`compile()` 必过，且可 `exec` 实际计算（如 `factorial(5) == 120`），
并附带 `difflib.unified_diff` 供演示「改了什么」。

### 4. 故障注入

`options["faults"]` 为规则列表，每条规则独立计数（`at_call` / `times` /
`model`），**不依赖全局随机**，因此故障行为也可精确复现。

## 备选方案

| 方案 | 否决原因 |
|---|---|
| 用当前时间 / 随机种子做输出 | 不可复现，恢复测试与 CI 无法断言 |
| 固定单一回复模板 | 无法区分角色与任务，端到端演练失真 |
| 用真实 LLM 但截断 | 仍需要 Key，违背「零 Key」目标 |
| 只返回伪代码字符串 | 无法满足「代码可运行」的 AC-A2 |

## 影响

- 下游**不要**依赖 Mock 输出的具体内容做断言（除了确定性本身），
  它只承诺「可复现 + 可运行」；
- 需要不同输出的场景（多轮探索）请通过 `loop_index` / `task_key` 区分，
  不要期望 Mock 自发产生多样化；
- 生产环境必须替换为真实 Provider。
