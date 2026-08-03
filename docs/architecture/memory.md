# 四层记忆与上下文治理（memory 包）

> 对应工作包：WP5（上下文治理与治理层）。本文解释 `src/loopforge/memory/` 的
> 设计、入口与给下游（WP4/WP6/WP7）的使用方式。

## 1. 为什么需要这一层

现有 Agent 框架都是「短程」的：跑几十轮后上下文爆炸、关键约束被挤掉、
token 成本失控。LoopForge 的目标是**长程自主编程**（16 天无人干预、
265 commits），因此上下文治理是核心差异化：**跑得久 + 跑得对 + 跑得起**。

## 2. 五层结构（L0~L4）

| 层 | 名称 | 内容 | 默认配额 | 可压缩 |
|---|---|---|---|---|
| **L0** | Pinned Constraints | 架构约束 / API 契约 / 安全红线 | 8%（硬上限） | ❌ 永不压缩 |
| **L1** | Working Memory | 当前 Loop 轮次即时上下文 | 40% | 仅 L1 结构化裁剪 |
| **L2** | Episodic Memory | 本 Run 历史事件结构化条目 | 22% | ✅ 三级 |
| **L3** | Semantic Memory | 代码库知识 / 跨 Run 经验 | 20% | ✅ 重建而非丢弃 |
| **L4** | Milestone Summary | 递归摘要树 | 10% | ✅ 更高层摘要 |

实现文件：

```
memory/
├── manager.py          # ContextManager —— 冻结出口（§11.7）
├── context_pack.py     # ContextPack —— build_context 返回值
├── pinned.py           # PinnedConstraintStore —— L0 硬配额守卫
├── budget_allocator.py # ContextBudgetAllocator —— 层配额 + 空闲再分配
├── compressor.py       # 三级压缩（L1/L2 确定性）+ 保真度校验
├── summarizer.py       # 摘要树（L3，LLM 可选，确定性兜底）
├── salience.py         # 显著性打分 + 时间衰减 + 命中提升
├── layers/             # 五层实现
└── retrieval/          # BM25（零依赖）/ Embedding（默认关）/ RRF 融合
```

## 3. 铁律（TASK_BREAKDOWN §7.3.1）

| # | 规则 | 落地位置 |
|---|---|---|
| M1 | L0 永不压缩、永不裁剪，硬配额 8%；配额不足宁可裁 L2/L3 | `pinned.py::_enforce_quota` |
| M2 | 原文永不物理删除，压缩只写 `compressed_into` + `MemoryLineage` | `manager.py::maybe_compress` |
| M3 | 每轮 `check_fidelity()` 校验全部 Pinned 约束；违背发 `CONSTRAINT_VIOLATION` 并暂停 | `manager.py::check_fidelity` |
| M4 | 压缩分级降级 L1→L2→L3，禁止一上来调 LLM | `compressor.py::compress` |

## 4. 上下文装配流程（build_context）

1. `ContextBudgetAllocator.allocate(token_budget)` 切分五层配额；
2. 强制注入全部 L0 Pinned Constraints（超配额按优先级截断 + 告警）；
3. 召回 L1 工作记忆 + L2/L3 BM25 + L4 里程碑摘要；
4. 贪心装填到各层配额，超出部分进 `dropped_items`（绝不静默丢弃）；
5. 超预算时内存内分级裁剪（L1 结构化 → L2 抽取）。

## 5. 压缩保真度三重保障

1. **原文永不删除**：`memory_items` 只标记 `compressed_into`；
2. **压缩谱系**：每次 L3 压缩写 `memory_lineage`（summary_id ↔ source_item_id
   ↔ `info_loss_score`）；
3. **保真度校验**：`check_fidelity` 把每条约束提取为关键事实
   （`extract_key_facts`），核对压缩/计划文本是否仍覆盖；缺失即 violation。

## 6. BM25（零外部依赖）

- 纯 Python，倒排表可选持久化到 `inverted_index` 表；
- 分词复用 `retrieval/tokenizer.py`：中文 bigram + 代码标识符拆分
  （`getUserName` → `get user name getusername`）；
- `k1=1.2, b=0.75` 可配；增量索引（重复 doc_id 覆盖，不触发全量重建）；
- EmbeddingRetriever **默认关闭**，不可用时优雅降级到 BM25（RRF 融合）。

## 7. 下游调用姿势

```python
from loopforge.memory import ContextManager

cm = ContextManager(session_factory=factory)   # 或省略用全局
pack = await cm.build_context(
    run_id=run_id, task_id=task_id,
    role=RoleName.CODER, token_budget=128_000,
)
messages = pack.to_messages()                  # 直接投喂 Provider
result = await cm.maybe_compress(run_id=run_id, reason="token_threshold")
report = await cm.check_fidelity(run_id=run_id, plan_text=text)
```

## 8. 测试

`tests/unit/memory/`：`test_bm25.py`、`test_compressor.py`、
`test_budget_allocator.py`、`test_pinned.py`。DoD 见 TASK_BREAKDOWN §7.4。
