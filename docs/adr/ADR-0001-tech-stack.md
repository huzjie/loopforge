# ADR-0001：技术栈选型

- **状态**：已接受（Accepted）
- **日期**：W0 波次 · WP1 交付
- **决策者**：WP1 内核与数据底座 owner
- **影响范围**：全部 8 个 WP

---

## 背景

LoopForge 是一个长时运行的自主软件工程 Agent 编排系统。技术栈必须同时满足：

1. **长时运行 + 高并发 I/O**：单 Run 可持续数小时，同时挂着数十个 LLM 调用、工具调用、Git 操作 —— 全是 I/O 密集。
2. **本地零依赖起步**：开发者 `git clone` 后应能不装 Docker、不装数据库直接跑通。
3. **生产可水平扩展**：多 Worker 争抢同一任务队列，必须有强并发正确性保证。
4. **契约先行**：8 个 WP 并行开发，公共词汇表（枚举/事件/错误码）必须能被静态检查卡住。

## 决策

| 层 | 选型 | 版本 |
|---|---|---|
| 语言 | Python | ≥ 3.11 |
| 异步运行时 | asyncio（标准库） | — |
| ORM | SQLAlchemy | 2.0（异步 + `Mapped[]` 声明式） |
| 迁移 | Alembic | 1.13+ |
| 数据库 | SQLite（`aiosqlite`）/ PostgreSQL 16（`asyncpg`） | 双方言 |
| 数据校验 | Pydantic | v2 |
| 配置 | pydantic-settings + YAML | 七层覆盖 |
| 日志 | structlog | JSON / Console 双渲染 |
| ID | ULID | 26 字符 |
| 测试 | pytest + pytest-asyncio（`asyncio_mode=auto`） | — |
| 静态检查 | ruff + mypy `--strict` | — |

---

## 理由

### 为什么 Python 3.11+

- `asyncio.TaskGroup` 与 `except*` 让「一批并发 LLM 调用，任一失败要能干净收敛」写得出来。
- `typing.Self`、`StrEnum`、更快的解释器。3.10 缺 `TaskGroup`，不考虑。
- Agent 生态（LLM SDK、AST 工具、测试框架）在 Python 上最厚。

### 为什么 asyncio 而不是线程池 / Celery

系统瓶颈 100% 是 I/O 等待（LLM 推理、网络、子进程）。
- 线程池：GIL 下无收益，还要处理线程安全。
- Celery：引入 Broker（Redis/RabbitMQ）破坏「本地零依赖起步」，且我们的任务分发需要**数据库级 CAS 语义**，Celery 的 at-least-once 投递反而要额外去重。

我们把「队列」直接建在 `tasks` 表上（见 `data-model.md` §4），并发正确性靠数据库事务与部分唯一索引兜底，不靠中间件。

### 为什么 SQLAlchemy 2.0 异步而不是 SQLModel / Tortoise / 裸 SQL

- **2.0 的 `Mapped[]` 声明式**能被 mypy `--strict` 完整检查，这是 8 WP 并行的刚需。
- 需要**条件 UPDATE 拿 rowcount**、**部分唯一索引**、**方言中立自定义类型**（`TypeDecorator`）—— SQLAlchemy Core 全部原生支持，Tortoise 不行。
- SQLModel 是 SQLAlchemy + Pydantic 的薄封装，但它把「持久化模型」和「API DTO」耦合成一个类。我们**刻意分离** `models/`（ORM，31 表）与 `schemas/`（DTO，158 个导出）：ORM 会有软删除列、版本列、fence token 这些不该出现在 API 响应里的东西。
- 裸 SQL 会让双方言支持退化成手写两套。

### 为什么 SQLite + PostgreSQL 双方言

- SQLite 满足「零依赖起步」和「测试跑得飞快」（内存库 + `StaticPool`，全量单测 8 秒内）。
- PostgreSQL 16 是生产目标：多 Worker 真并发、`JSONB` 索引、连接池。
- 双方言的代价由 WP1 一次性吃掉（`db/types.py` 4 个 `TypeDecorator`），换来上层 7 个 WP 完全无感。具体权衡见 **ADR-0002**。

### 为什么 Pydantic v2

- Rust 核心，校验快一个数量级；`model_config = {"extra": "forbid"}` 让契约违例在**构造时**就炸，而不是流到下游。
- 事件 payload 用 Pydantic 模型 + `EVENT_PAYLOAD_MAP` 强绑定，72 个事件类型 100% 覆盖，跨 WP 传参不会靠猜。

### 为什么 ULID 而不是 UUID4 / 自增整数

- **单调递增**：前 48 bit 是毫秒时间戳，主键索引写入不随机跳页，B-Tree 不碎片化。
- **可排序**：`ORDER BY id` 近似等于按创建时间排，省一个索引。
- **客户端可生成**：不需要 round-trip 拿 ID，一个事务内可以先构造对象图再落库。
- 自增整数在多 Worker / 未来分库下会成为争抢点，且 ID 会泄露业务量。
- `VARCHAR(26)` 在两个方言上表现一致，比 `UUID` 类型少一层方言差异。

### 为什么 structlog

需要把 `trace_id / run_id / task_id / step_id` 透传到每一条日志。structlog 的 `contextvars` 集成 + 处理器链让我们能在管线里挂**脱敏处理器**（24 条 Secret 正则），保证密钥不会因为一句 `log.info(payload=...)` 泄露到日志文件。标准库 `logging` 要做到这些得手写 Filter + Adapter，可读性差。

### 为什么 ruff + mypy --strict

8 个 WP 并行，唯一能规模化守住契约的是静态检查：
- ruff 自定义规则**禁用 `datetime.now(` / `time.time(`**（全仓时间只走 `core.clock.Clock`，否则测试无法冻结时间）。
- mypy `--strict` 保证 `models/` `schemas/` `repositories/` 的签名与 §11 冻结清单逐字一致。

---

## 后果

### 正面

- 本地 `pip install -e .` + `alembic upgrade head` 即可跑通，无外部服务依赖。
- 全量单测 251 条 8 秒内跑完，CI 反馈快。
- 上层 WP 只依赖 `core` / `db` / `models` / `schemas` 的公开签名，不接触方言差异。

### 负面 / 需承担的成本

- **SQLite 与 PostgreSQL 行为差异**必须靠测试覆盖（尤其部分唯一索引、`NUMERIC` 精度、事务隔离级别）。缓解：迁移测试在两个方言上各跑一遍。
- **SQLite 无真并发写**：单测里的 1000 争抢者 CAS 测试用单连接顺序化验证「条件 UPDATE 的 rowcount 语义」，真并发压测必须在 PostgreSQL 上补。这一点在测试用例注释中已显式标注。
- **asyncio 全链路**：任何同步阻塞调用（如某个 SDK 只有同步版）都必须 `run_in_executor` 包一层，否则拖垮事件循环。已在 `code-style.md` 列为红线。
- Pydantic v2 与部分老库不兼容，选型 Provider SDK 时需确认。

## 备选方案（已否决）

| 方案 | 否决理由 |
|---|---|
| FastAPI + SQLModel + Celery + Redis | 中间件依赖破坏零依赖起步；SQLModel 耦合 ORM 与 DTO；Celery 投递语义与 CAS 需求错配 |
| Node.js / TypeScript + Prisma | Agent 生态（AST 分析、测试运行器集成）弱于 Python；Prisma 迁移不可控 |
| Go + sqlc | 并发模型优秀，但 LLM/Agent 生态与团队熟练度不匹配，迭代速度会掉一档 |
| 纯 PostgreSQL（放弃 SQLite） | 破坏「clone 即跑」；测试需要起容器，CI 慢 3-5 倍 |
