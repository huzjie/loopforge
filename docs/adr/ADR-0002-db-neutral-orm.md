# ADR-0002：方言中立 ORM —— 为什么不用 pgvector，为什么坚持方言中立

- **状态**：已接受（Accepted）
- **日期**：W0 波次 · WP1 交付
- **决策者**：WP1 内核与数据底座 owner
- **影响范围**：WP1（`db/types.py`、`models/`、`alembic/`）、WP5（记忆与检索）、WP6（治理）
- **相关**：ADR-0001（技术栈选型）、`docs/architecture/data-model.md`

---

## 背景

系统要在两个数据库上跑同一份代码：

- **SQLite**（`aiosqlite`）：开发者本地、CI 单测、单机部署。目标是 `git clone` 后不装任何服务就能跑通全流程。
- **PostgreSQL 16**（`asyncpg`）：生产，多 Worker 并发争抢任务。

「双方言」有两种做法：
- **A. 分支实现**：模型里按 `dialect.name` 写 if/else，PostgreSQL 用 `JSONB`/原生 `ENUM`/`pgvector`，SQLite 用降级实现。
- **B. 方言中立**：定义一层抽象类型，模型代码只见抽象类型，方言差异全部收敛在这一层。

同时，记忆检索（WP5）需要语义相似度搜索，最直接的方案是 PostgreSQL 的 `pgvector` 扩展。

## 决策

### 决策 1：采用方案 B（方言中立），差异全部收敛到 `db/types.py`

`models/` 下 16 个文件**只允许**使用 SQLAlchemy 通用类型和以下 4 个自定义 `TypeDecorator`。**禁止 `from sqlalchemy.dialects.postgresql import ...`**（ruff 规则卡住）。

| 抽象类型 | SQLite | PostgreSQL | 语义 |
|---|---|---|---|
| `JsonType` | `TEXT` + `json.dumps/loads` | `JSONB` | Python `dict`/`list` 透明读写 |
| `UtcDateTime` | `TEXT`（ISO8601 UTC） | `TIMESTAMPTZ` | 出入库强制 tz-aware UTC，naive 直接抛错 |
| `StrEnumType` | `VARCHAR` | `VARCHAR` | 存枚举 **value 字符串** |
| `DecimalMoney` | `NUMERIC` | `NUMERIC(18, 6)` | `Decimal` 进出，绝不经过 float |

### 决策 2：**不使用 pgvector**，语义检索用「方言中立倒排索引 + 可插拔向量后端」

记忆检索落地为三张表：`inverted_index`（词元 → 条目）、`index_stats`（词频/文档数，供 BM25 打分）、`memory_items`（主存储）。向量检索定义为 **WP5 的可选后端接口**，不进入 WP1 的 schema。

### 决策 3：**不使用原生 ENUM 类型**

枚举一律 `VARCHAR` + `StrEnumType`，加 `CHECK` 约束（在 `0006` 迁移中统一添加）。

---

## 理由

### 为什么不用 pgvector

**1. 它会直接击穿「零依赖起步」这条产品底线。**
`pgvector` 是 PostgreSQL **扩展**，需要 `CREATE EXTENSION vector`，这要求：装 PostgreSQL、有超级用户权限、扩展已编译安装。SQLite 上**没有任何等价物**。一旦 schema 依赖 `vector` 列类型，`alembic upgrade head` 在 SQLite 上直接失败 —— 开发者本地跑不起来，CI 必须起容器，整个「clone 即跑」的承诺作废。这不是可以靠 if/else 绕开的：**列类型是 schema 的一部分，不是运行时行为**。

**2. 当前规模下，向量检索不是瓶颈，收益被高估。**
单个 Run 的记忆条目量级在 10³~10⁴，BM25 倒排 + 时间衰减 + 层级加权的召回质量，在「代码上下文检索」这个具体场景下不弱于纯向量 —— 因为代码检索里**精确词元匹配（函数名、类名、错误码、文件路径）比语义相似更重要**。向量最擅长的「换个说法也能找到」，恰恰是我们最不需要的能力：`FencedOutError` 就是 `FencedOutError`，没有近义词。

**3. 引入 embedding 会把「检索」变成「网络调用 + 成本 + 失败模式」。**
每条记忆入库都要调 embedding 模型：多一次网络往返、多一份 token 成本、多一类失败（限流/超时/供应商宕机），还要处理「embedding 模型换版本后旧向量作废」的迁移问题。在 WP1 阶段为一个尚未验证收益的能力吃下这些复杂度，不划算。

**4. 我们没有关上门 —— 而是把门做成了接口。**
`memory_items` 预留了 `embedding_ref`（外部向量存储的引用键）与 `embedding_model` 列。WP5 可以实现 `VectorBackend` 协议，把 Qdrant / pgvector / 本地 FAISS 挂进去做**混合检索**（BM25 召回 + 向量重排）。届时：
- 向量数据**存在外部系统**，不进 LoopForge 的 schema；
- 主库 schema 在两个方言上依然一致；
- 没配向量后端时，系统自动退化为纯 BM25，**功能可用而非报错**。

这是「延迟决策到信息更充分时」，不是「拒绝向量检索」。

### 为什么不用原生 ENUM

- PostgreSQL 原生 `ENUM` 是数据库对象，**加一个值要 `ALTER TYPE`**，且在事务中受限；SQLite 根本没有。用 `VARCHAR` 后，枚举演进只是 Python 侧改代码 + 一条 CHECK 约束迁移。
- SQLAlchemy 的 `Enum(native_enum=False)` 也能做到，但它默认存 **name** 而非 **value**。我们的枚举 name 与 value 可能不一致，跨 WP 序列化必须以 **value** 为准（事件 payload、API 响应、日志都用 value）。自己写 `StrEnumType` 语义明确、无歧义。
- 未知枚举值的失败点因此可控：反序列化时抛 `ValidationError` 带错误码，而不是数据库层抛一个方言相关的怪异异常。

### 为什么金额用 `DecimalMoney` 而不是 float

预算治理（WP6）要做累加与阈值比较。float 的 `0.1 + 0.2 != 0.3` 会让「花了 100.00 却判定没超 100」这类 bug 随机出现且极难复现。`NUMERIC(18,6)` 支持到微美元级单价，`Decimal` 全程无损。

### 为什么时间要专门做一个 `UtcDateTime`

SQLite 没有时区类型，存进去什么样取出来什么样；PostgreSQL 的 `TIMESTAMPTZ` 会做时区转换。不统一的话，同一段代码在两个库上算出的「租约是否过期」会不一致 —— 这是会导致**重复获取任务**的正确性 bug，不是显示问题。`UtcDateTime` 在 `process_bind_param` 里拒绝 naive datetime，在 `process_result_value` 里强制附加 UTC tzinfo。配合 `core.clock.Clock`（禁用 `datetime.now()`），测试可以完全冻结时间。

---

## 后果

### 正面

- 一套 `models/` 代码、一套迁移链在两个方言上跑通，上层 7 个 WP 完全无感于方言差异。
- 本地/CI 用 SQLite 内存库，全量 251 条测试 8 秒内跑完。
- 记忆检索无外部服务依赖，`git clone` 即可验证端到端流程。
- 向量能力保留为可插拔后端，不构成技术债，是显式的扩展点。

### 负面 / 需承担的成本

- **放弃了 PostgreSQL 的部分性能红利**：`JSONB` 的 GIN 索引、`JSONB` 路径查询在 SQLite 侧无等价物，因此**禁止对 JSON 列做条件查询**。需要被查询的字段必须提升为独立列。这条已写入 `code-style.md` 红线。
- **BM25 打分逻辑要自己写并自己维护**（`index_stats` 的词频统计、分词策略），不能白嫖数据库全文检索（PostgreSQL `tsvector` 同样是方言特性）。
- **中文分词**需要额外处理，纯空格切分对中文无效 —— WP5 需引入分词器，这是已知待办。
- **两个方言的行为差异必须靠测试兜住**：`tests/unit/db/test_migrations.py` 覆盖迁移链与建表；真并发正确性（SQLite 无真并发写）必须在 PostgreSQL 上补压测。
- 每新增一种特殊列类型，都要在 `db/types.py` 写一个 `TypeDecorator` 并为两个方言各测一遍。

## 备选方案（已否决）

| 方案 | 否决理由 |
|---|---|
| PostgreSQL + pgvector 单方言 | 破坏零依赖起步；CI 必须起容器；本地开发门槛陡增 |
| 双 schema（PG 用 pgvector，SQLite 降级） | 两套迁移链、两套模型分支，维护成本随表数线性增长；「本地能过、生产挂掉」的经典陷阱 |
| 主库 SQLite/PG + 独立向量库（WP1 即引入） | 方向正确但**时机太早**：WP1 是全项目地基，引入外部服务依赖会阻塞其余 7 个 WP 的本地开发。已降级为 WP5 可选后端 |
| 用 SQLAlchemy `Enum(native_enum=False)` | 默认存 name 而非 value，与跨 WP 序列化契约（以 value 为准）冲突 |
