# LoopForge 数据模型（WP1 冻结版）

> 版本：v1.0 · 归属：WP1 内核与数据底座 · 状态：**已冻结**
>
> 本文是全项目唯一的数据模型事实来源。其他 WP **只读不改**：不得新增 `models/` 文件、不得新增 Alembic 迁移（见 `docs/TASK_BREAKDOWN.md` §13.2）。
> 如需变更表结构，必须提 ADR 并由 WP1 owner 执行。

---

## 1. 总览

| 项 | 值 |
|---|---|
| ORM | SQLAlchemy 2.0（`Mapped[]` / `mapped_column()` 声明式，全异步） |
| 迁移 | Alembic，revision 链 `0001 → 0006`，**单 head** |
| 目标方言 | SQLite（开发/测试，`aiosqlite`）与 PostgreSQL 16（生产，`asyncpg`） |
| ORM 表数 | **31**（`Base.metadata.tables`）；`alembic upgrade head` 后物理表 **32**（含 `alembic_version`） |
| 模型文件 | `src/loopforge/models/` 共 16 个（`__init__.py` + 15 个领域文件） |
| 主键 | 全部 `VARCHAR(26)` ULID（`core.ids.new_ulid()`），**不使用自增整数** |
| 时间 | 全部 `UtcDateTime`，写入前统一归一到 UTC；**禁止 `datetime.now()`**，一律走 `core.clock.Clock` |

### 1.1 为什么设计文档说 26 张、实际是 31 张

`ARCHITECTURE.md` 的 26 张是**领域主表**计数。实现额外落地了 5 张**基础设施表**，它们不属于业务领域但为 DoD 所必需：

| 增补表 | 归属文件 | 存在理由 |
|---|---|---|
| `idempotency_records` | `models/step.py` | 副作用幂等键去重（`SideEffectClass.IDEMPOTENT` 的落地载体） |
| `memory_lineage` | `models/memory.py` | 记忆压缩血缘（摘要 → 原始条目），支撑「可解释的遗忘」 |
| `index_stats` | `models/memory.py` | 倒排索引统计，避免每次检索全表 `COUNT(*)` |
| `webhook_deliveries` | `models/evolution.py` | 出站 webhook 投递与重试状态机 |
| `checkpoints` | `models/run.py` | Run 级快照，`RECOVERING` 状态回滚锚点 |

这 5 张表在 §11 冻结接口清单中已有对应 Repository，属于**契约内增补**，不是范围蔓延。

---

## 2. 表清单（按领域分组）

### 2.1 项目与代码库（3）

| 表 | 模型 | 关键列 | 说明 |
|---|---|---|---|
| `projects` | `Project` | `id, slug(uniq), name, state, settings(JSON)` | 顶层容器 |
| `repos` | `Repo` | `id, project_id, url, default_branch, provider` | 一个项目可挂多个仓库 |
| `artifacts` | `Artifact` | `id, run_id, step_id, kind, uri, sha256, size_bytes` | 产物寻址用 `sha256` 而非路径 |

### 2.2 执行主链（5）

| 表 | 模型 | 关键列 | 说明 |
|---|---|---|---|
| `runs` | `Run` | `id, project_id, state, loop_index, max_loops, version` | 状态机 14 态（`RunState`） |
| `tasks` | `Task` | `id, run_id, task_key, state, priority, version, fence_token` | **并发正确性核心**，见 §4 |
| `leases` | `Lease` | `id, task_id, worker_id, state, fence_token, expires_at, version` | 租约状态机 4 态（`LeaseState`） |
| `steps` | `Step` | `id, task_id, run_id, state, phase, side_effect_class, idempotency_key` | 意图-确认两段式，见 §5 |
| `checkpoints` | `Checkpoint` | `id, run_id, kind, payload(JSON), created_at` | 恢复锚点 |

### 2.3 事件与审计（3）

| 表 | 模型 | 关键列 | 说明 |
|---|---|---|---|
| `events` | `Event` | `id, seq, run_id, event_type, payload(JSON), prev_hash, hash` | **哈希链**，见 §6 |
| `audit_logs` | `AuditLog` | `id, actor, actor_type, action, resource_type, resource_id, before, after, reason` | 人工/系统操作留痕 |
| `idempotency_records` | `IdempotencyRecord` | `id, idempotency_key(uniq), scope, result_ref, expires_at` | 副作用去重 |

### 2.4 记忆分层（6）

| 表 | 模型 | 层 | 说明 |
|---|---|---|---|
| `memory_items` | `MemoryItem` | WORKING/EPISODIC/SEMANTIC | 主存储，带 `layer` 判别列 |
| `pinned_constraints` | `PinnedConstraint` | PINNED | 用户硬约束，**永不压缩**，有配额上限 |
| `milestone_summaries` | `MilestoneSummary` | MILESTONE | 里程碑摘要 |
| `memory_lineage` | `MemoryLineage` | — | `summary_id → source_id` 多对多血缘 |
| `inverted_index` | `InvertedIndex` | — | 词元 → 条目倒排（方言中立全文检索，见 ADR-0002） |
| `index_stats` | `IndexStats` | — | 词频/文档数统计，供 BM25 打分 |

### 2.5 治理（6）

| 表 | 模型 | 说明 |
|---|---|---|
| `budgets` | `Budget` | `scope(RUN/DAY/ORG) + scope_ref + window` 三元组唯一 |
| `cost_records` | `CostRecord` | 逐次调用成本，`DecimalMoney(18,6)` |
| `policies` | `Policy` | 策略包，带 `version` 与发布态 |
| `policy_rules` | `PolicyRule` | 规则明细，`decision ∈ ALLOW/DENY/REQUIRE_APPROVAL` |
| `approvals` | `Approval` | 人工审批工单，超时动作可配 |
| `llm_calls` / `tool_calls` | `LlmCall` / `ToolCall` | 调用明细（见 2.6，计入治理口径） |

### 2.6 系统与身份（8）

| 表 | 模型 | 说明 |
|---|---|---|
| `workers` | `Worker` | Worker 注册与心跳 |
| `provider_configs` | `ProviderConfig` | 模型供应商配置，**Key 只存引用不存明文** |
| `agent_roles` | `AgentRole` | 5 个角色（`RoleName`）的 prompt/模型绑定 |
| `users` | `User` | 账号 |
| `api_keys` | `ApiKey` | 只存 `hash`，明文仅签发时返回一次 |
| `evolution_items` | `EvolutionItem` | 自演进待办 |
| `webhook_deliveries` | `WebhookDelivery` | 出站投递重试 |
| `llm_calls`, `tool_calls` | `LlmCall`, `ToolCall` | 调用明细表 |

---

## 3. 方言中立类型（`db/types.py`）

所有模型**只允许**使用下列 4 个自定义类型 + SQLAlchemy 通用类型。**禁止**直接引用 `sqlalchemy.dialects.postgresql.*`。

| 类型 | SQLite 落地 | PostgreSQL 落地 | 说明 |
|---|---|---|---|
| `JsonType` | `TEXT`（json 序列化） | `JSONB` | 统一 `dict`/`list` 读写 |
| `UtcDateTime` | `TEXT`（ISO8601） | `TIMESTAMPTZ` | 出入库强制 tz-aware UTC |
| `StrEnumType` | `VARCHAR` | `VARCHAR` | 存枚举 **value 字符串**，不用原生 ENUM（见 ADR-0002） |
| `DecimalMoney` | `NUMERIC` | `NUMERIC(18,6)` | 金额禁用 float |

Mixin（`db/mixins.py`）：`IdMixin`（ULID 主键）、`TimestampMixin`（`created_at`/`updated_at`）、`VersionMixin`（`version` 乐观锁）、`SoftDeleteMixin`（`deleted_at`）。

---

## 4. 并发正确性：CAS + Fence Token

`tasks` 是唯一的并发争抢点，靠三件事保证「零重复获取」：

### 4.1 乐观锁 CAS

```python
await uow.tasks.cas_transition(
    task_id=task.id,
    expected_state=TaskState.READY,
    new_state=TaskState.LEASED,
    expected_version=task.version,
)  # -> CasResult(ok: bool, version: int, ...)
```

底层是**单条条件 UPDATE**：

```sql
UPDATE tasks SET state = :new, version = :expected + 1, ...
WHERE id = :id AND state = :expected AND version = :expected
```

`rowcount == 1` 才算赢。失败返回 `CasResult.ok = False`，**不抛异常**（调用方决定重试还是放弃）。

### 4.2 租约与部分唯一索引

`leases` 上建**部分唯一索引**，从数据库层面杜绝一个 Task 同时两个活跃租约：

```sql
CREATE UNIQUE INDEX uq_leases_active_task ON leases (task_id) WHERE state = 'ACTIVE';
```

SQLite ≥ 3.8 与 PostgreSQL 均原生支持 partial index，无需方言分支。

### 4.3 Fence Token

`tasks.fence_token` 单调递增，每次成功获取租约 `+1`。所有对外副作用必须携带 fence token，`lease_repo.verify_fence(task_id, fence_token)` 校验失败抛 `FencedOutError` —— 用于阻断「僵尸 worker 拿着过期租约继续写」。

### 4.4 DoD 验证

`tests/unit/db/test_repositories.py::test_task_cas_1000_contenders_exactly_one_wins`
1000 个争抢者持相同 `expected_version` 发起 CAS，断言**有且仅有 1 个** `ok=True`。

### 4.5 关键索引

| 索引 | 表 | 定义 | 用途 |
|---|---|---|---|
| `ix_tasks_leasable` | `tasks` | `(state, priority DESC, created_at)` | 派发队列扫描 |
| `uq_leases_active_task` | `leases` | `(task_id) WHERE state='ACTIVE'` | 零重复获取 |
| `uq_events_run_seq` | `events` | `(run_id, seq)` | 事件序号唯一 |
| `uq_tasks_run_key` | `tasks` | `(run_id, task_key)` | 任务键幂等 |
| `uq_budgets_scope` | `budgets` | `(scope, scope_ref, window)` | 预算三元组唯一 |

> **`priority` 语义（易错点）**：**数值越大越优先**。`priority=90` 排在 `priority=10` 之前。

---

## 5. Step 的意图-确认两段式

`Step` 不是「执行完才记录」，而是**先记意图、后确认结果**，解决进程崩溃导致的「做没做过说不清」：

```
PENDING → INTENT_RECORDED → CONFIRMED
                          ↘ FAILED / ABANDONED / UNCERTAIN
```

- `INTENT_RECORDED`：副作用**即将**发生，已落库。
- `UNCERTAIN`：意图已记但结果未知（如超时）。**这是恢复流程的重点扫描对象**，`step_repo.list_unconfirmed(run_id=...)` 返回它们。
- `side_effect_class`（`PURE/IDEMPOTENT/PROBEABLE/UNSAFE`）决定 `UNCERTAIN` 的处理策略：`PURE` 直接重放、`IDEMPOTENT` 带 key 重放、`PROBEABLE` 先探测、`UNSAFE` 转人工审批。

---

## 6. 事件哈希链

`events` 表是**只追加**的。每行：

```
hash = sha256(canonical_json({prev_hash, run_id, seq, event_type, payload, created_at}))
```

- `seq` 在 `(run_id)` 内单调递增，`uq_events_run_seq` 保证不重不漏。
- `prev_hash` 指向同一 run 的上一条；首条为全零串。
- 校验入口：`event_repo.verify_chain(run_id)` → `ChainVerifyResult`。
- **事件名冻结为 UPPER_SNAKE 过去式**（`RUN_STARTED`、`TASK_LEASED`），共 **72** 个 `EventType`，`EVENT_PAYLOAD_MAP` 覆盖率 100%。

> `payload` 落库前必过 `core.redaction.redact_mapping()`，24 条 Secret 正则 + 敏感键名黑名单。

---

## 7. 事务边界：UnitOfWork

**所有**数据库写入必须在 `UnitOfWork` 内完成，禁止裸用 `AsyncSession`：

```python
from loopforge.db.uow import UnitOfWork

async with UnitOfWork() as uow:
    run = await uow.runs.create(project_id=pid, state=RunState.CREATED)
    await uow.events.append(run_id=run.id, event_type=EventType.RUN_CREATED, payload={...})
    # 退出即 commit；异常自动 rollback
```

- UoW 内的事件先进 `PendingEvent` 暂存，**commit 成功后**才 fanout 到 `EventBus`，避免「事务回滚了但事件已发出去」。
- 仓储属性冻结名见 §11.2：`projects / runs / tasks / leases / steps / events / memory / governance / system`（另有细粒度直达属性，如 `uow.budgets`）。

---

## 8. 迁移链

| revision | 内容 |
|---|---|
| `0001_initial_schema` | projects / repos / runs / tasks / leases / steps |
| `0002_events_and_audit` | events / audit_logs / idempotency_records / artifacts |
| `0003_memory_layers` | memory_items / pinned_constraints / milestone_summaries / memory_lineage / inverted_index / index_stats |
| `0004_governance` | budgets / cost_records / policies / policy_rules / approvals / llm_calls / tool_calls |
| `0005_system_tables` | workers / provider_configs / agent_roles / users / api_keys / evolution_items / webhook_deliveries / checkpoints |
| `0006_indexes_and_constraints` | 全部部分唯一索引、复合索引、CHECK 约束 |

约束：**单 head**（`scripts/check_alembic_heads.py` 在 CI 卡住多 head）；已合入的 revision 内容不可变；禁止 `--autogenerate` 重生成。
回归测试见 `tests/unit/db/test_migrations.py`（链完整性、`upgrade head` 建表覆盖、`downgrade base` 可逆）。
