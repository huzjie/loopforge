# 参与 LoopForge 开发

欢迎。本文是**动手前必读**的最短路径：环境怎么起、代码怎么提、边界在哪里。
风格细则见 [`docs/contributing/code-style.md`](docs/contributing/code-style.md)，数据模型见 [`docs/architecture/data-model.md`](docs/architecture/data-model.md)。

---

## 1. 环境准备

要求：**Python ≥ 3.11**、Git。不需要 Docker，不需要预装数据库。

```bash
git clone <repo-url> && cd loopforge

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements-dev.txt
pip install -e .
pre-commit install
```

或者一条命令：`make all`（= 装依赖 + 全量校验）。

### 验证装好了

```bash
python -c "import loopforge; print(loopforge.__version__)"
alembic upgrade head     # SQLite 建出 32 张表（31 业务表 + alembic_version）
pytest tests/ -q         # 应当全绿
```

默认数据库是 `data/loopforge.db`（SQLite）。想用 PostgreSQL：

```bash
export LOOPFORGE_DB__URL="postgresql+asyncpg://user:pass@localhost:5432/loopforge"
alembic upgrade head
```

---

## 2. 常用命令

| 命令 | 作用 |
|---|---|
| `make verify` | **提 PR 前必跑**：lint + typecheck + test |
| `make lint` / `make fmt` | ruff 检查 / 自动修复 |
| `make typecheck` | mypy `--strict` |
| `make test` / `make cov` | 跑测试 / 带覆盖率 |
| `make migrate` / `make downgrade` | `alembic upgrade head` / `downgrade base` |
| `make reset-db` | 清空并重建库 |
| `make seed` | 写入种子数据 |
| `make heads` | 检查迁移头（**必须恰好一行**） |
| `make clean` | 清理缓存产物 |

Windows 无 `make` 时，直接照抄 `Makefile` 里的命令即可，全部是相对路径。

---

## 3. 项目结构

```
src/loopforge/
├── core/         # 内核：配置、日志、枚举、事件、错误、时钟、脱敏、韧性 (14)
├── db/           # 引擎、会话、UoW、方言中立类型、11 个仓储 (19)
├── models/       # SQLAlchemy ORM，31 张表 (16)
├── schemas/      # Pydantic v2 DTO (15)
├── constants.py
├── __main__.py
└── __init__.py
alembic/          # 迁移链 0001 → 0006（单 head）
config/default.yaml   # 8 个配置节的全量键
tests/            # 镜像源码结构
scripts/          # CI 门禁脚本
docs/             # 架构、ADR、任务分解
```

**依赖方向是单向的**：`core` ← `db` ← `models`/`schemas` ← 业务包。
`core` 不得 import `db`；`schemas` 不得 import `models`。违反会被 CI 拦下。

---

## 4. 分层边界（重要）

LoopForge 由 8 个工作包（WP）并行开发。WP1 交付的 `core` / `db` / `models` / `schemas` 是**冻结地基**：

### 4.1 其他 WP **禁止**做的事

| 禁止 | 替代做法 |
|---|---|
| 新增 `models/` 文件或改表结构 | 提 ADR → 由 WP1 owner 执行迁移 |
| 新增 `alembic/versions/` 迁移 | 同上。迁移链由 WP1 独家维护 |
| 修改已合入的 revision 内容 | 只能追加新 revision（且需 WP1 执行） |
| 在 `config/default.yaml` 新增配置**节** | 8 个节已预留齐全，只改值不加节 |
| 改动 `core/enums.py` / `event_types.py` / `error_codes.py` 已有成员 | 只允许**追加**，且需同步更新 `docs/TASK_BREAKDOWN.md` §11/§12 |
| 深入内部模块路径导入 | 只从包顶层导入：`from loopforge.schemas import TaskRead` |

### 4.2 冻结契约在哪里

`docs/TASK_BREAKDOWN.md`：
- **§11.1** 11 组枚举的全量成员
- **§11.2** 仓储与 UoW 的逐字签名
- **§12.2** 事件类型命名（`UPPER_SNAKE` 过去式）
- **§13** 冲突规避规则

**代码与 §11 不一致时，以 §11 为准 —— 改实现，不改契约。**
如果确信是契约本身有问题，先改 `TASK_BREAKDOWN.md` 并在 PR 描述里显式说明，不要默默改实现。

---

## 5. 开发流程

1. **认领任务**：从 `docs/TASK_BREAKDOWN.md` 里自己那一节取「文件清单 + 实现顺序 + DoD」。
2. **建分支**：`feat/wp<n>-<简述>` 或 `fix/wp<n>-<简述>`。
3. **先写测试**：修 bug 必须先写一条能复现失败的回归测试。
4. **实现**：遵守 [`code-style.md`](docs/contributing/code-style.md) 的 9 条红线。
5. **自检**：`make verify` 全绿。
6. **提 PR**：对照下面的清单。

### 提交信息

Conventional Commits：

```
feat(db): 为 LeaseRepository 增加 fence token 校验

acquire_lease_cas 成功后写入递增 fence_token，
verify_fence 校验失败抛 FencedOutError，阻断僵尸 worker 写入。

Refs: WP1
```

`type` ∈ `feat|fix|docs|refactor|test|chore|perf`，`scope` 用包名。

### PR 自检清单

- [ ] `make verify` 全绿（lint + mypy strict + pytest）
- [ ] 新增/修改逻辑有对应测试，覆盖率不下降（≥ 80%）
- [ ] `grep -rn "TODO\|FIXME" src/` 为 0
- [ ] 无 `datetime.now()` / `time.time()`（`python scripts/check_clock_usage.py`）
- [ ] `alembic heads` 只有一行（`python scripts/check_alembic_heads.py`）
- [ ] 未越界修改其他 WP 的文件（见 §4.1）
- [ ] 签名与 `TASK_BREAKDOWN.md` §11 逐字一致
- [ ] 涉及行为变更的，已更新对应文档

---

## 6. 高频踩坑

**`priority` 是数值越大越优先。** `priority=90` 排在 `priority=10` 前面。索引 `ix_tasks_leasable` 用的是 `priority DESC`。

**CAS 失败不是异常。** `cas_transition` 返回 `CasResult(ok=False)`，不抛异常。竞争失败是正常业务分支，调用方自己决定重试还是放弃。

**CAS 之后不要读 ORM 对象的旧属性。** 条件 UPDATE 走的是 Core 语句，session 里的 ORM 实例是**陈旧**的。要么在 CAS 前把旧值快照下来，要么 CAS 后 `refresh`。

**事件名是 `UPPER_SNAKE` 过去式**（`RUN_STARTED`），不是点分小写。

**枚举存的是 value 不是 name。** 序列化一律以 value 为准。

**时间必须走 `Clock`。** 直接 `datetime.now()` 会被 CI 拦下，且会让时间相关测试无法冻结。

**写库必须在 `UnitOfWork` 里。** 裸用 `AsyncSession` 会绕过事务边界与事件 fanout 时机。

**不要对 JSON 列做条件查询。** SQLite 上它就是 `TEXT`，没有索引。需要查询的字段提升为独立列。

**金额用 `Decimal`，不用 float。**

---

## 7. 报 Bug

请附：复现步骤、期望行为、实际行为、`python -V` 与操作系统、以及**最小复现测试用例**（有的话）。
涉及数据库的，注明用的是 SQLite 还是 PostgreSQL —— 两个方言的行为差异是已知风险点。
