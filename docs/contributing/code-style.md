# LoopForge 代码风格规范

> 适用范围：全部 8 个 WP。本文的「红线」条目由 ruff / mypy / CI 脚本自动卡住，**不是建议，是门禁**。

---

## 1. 工具链

| 工具 | 命令 | 门禁 |
|---|---|---|
| ruff（lint + format） | `make lint` / `make fmt` | 零告警 |
| mypy `--strict` | `make typecheck` | `core` / `db` / `models` / `schemas` 零错误 |
| pytest | `make test` | 全绿，覆盖率 ≥ 80% |
| Alembic head 检查 | `python scripts/check_alembic_heads.py` | 单 head |
| Clock 用法检查 | `python scripts/check_clock_usage.py` | 零 `datetime.now(` / `time.time(` |
| pre-commit | `pre-commit install` | 提交前自动跑上述检查 |

一次跑全：`make verify`（= `lint` + `typecheck` + `test`）。

---

## 2. 红线（违反即 CI 失败）

### 2.1 时间只走 Clock

```python
# ❌ 禁止
from datetime import datetime
now = datetime.now()
now = datetime.utcnow()
ts = time.time()

# ✅ 正确
from loopforge.core.clock import Clock, SystemClock

class MyService:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    def do(self) -> None:
        now = self._clock.now()  # 永远 tz-aware UTC
```

理由：租约过期、预算窗口、重试退避全部依赖时间。测试必须能用 `FrozenClock` 冻结时间，否则「租约过期」这类逻辑无法确定性验证。
由 `scripts/check_clock_usage.py` 全仓扫描。

### 2.2 不得引入方言特定类型

```python
# ❌ 禁止
from sqlalchemy.dialects.postgresql import JSONB, UUID, ARRAY

# ✅ 正确
from loopforge.db.types import JsonType, UtcDateTime, StrEnumType, DecimalMoney
```

见 ADR-0002。

### 2.3 不得对 JSON 列做条件查询

JSON 列在 SQLite 上是 `TEXT`，无索引、无路径查询。**需要被 `WHERE` / `ORDER BY` 用到的字段，必须提升为独立列。**

### 2.4 金额不得用 float

```python
# ❌  cost: float = 0.001
# ✅  cost: Decimal = Decimal("0.001")   # 列类型 DecimalMoney
```

### 2.5 不得新增 ORM 模型文件与迁移文件

`models/` 16 个文件、`alembic/versions/` 0001~0006 由 **WP1 owner 独家维护**。其他 WP 需要表结构变更 → 提 ADR → WP1 执行。
已合入的 revision **内容不可变**，禁止 `--autogenerate` 重生成。

### 2.6 不得裸用 AsyncSession 写库

```python
# ❌ 禁止
session = AsyncSession(engine)
session.add(obj)

# ✅ 正确
async with UnitOfWork() as uow:
    await uow.runs.create(...)
```

理由：UoW 统一事务边界，并保证事件在 **commit 成功后**才 fanout。

### 2.7 不得在 async 函数里做同步阻塞调用

```python
# ❌ requests.get(...) / subprocess.run(...) / time.sleep(...)
# ✅ httpx.AsyncClient / asyncio.create_subprocess_exec / await asyncio.sleep(...)
```

只有同步 SDK 时，用 `await asyncio.get_running_loop().run_in_executor(None, fn, *args)` 包一层。

### 2.8 不得留 TODO / FIXME / pass 占位

`src/` 下 `grep -rn "TODO\|FIXME"` 必须为 0。要么实现，要么明确抛 `NotImplementedError` 并附错误码，要么写进 `evolution_items`。

### 2.9 不得裸抛内置异常

```python
# ❌ raise ValueError("task not found")
# ✅ raise TaskNotFoundError(details={"task_id": task_id})
```

所有异常继承 `LoopForgeError`，自带 `code` / `http_status` / `details` / `retryable`。

---

## 3. 命名约定（跨 WP 冻结）

| 对象 | 约定 | 示例 |
|---|---|---|
| 枚举成员 | `UPPER_SNAKE`，value == name | `TaskState.LEASED` → `"LEASED"` |
| 事件类型 | `UPPER_SNAKE` **过去式** | `RUN_STARTED`、`TASK_LEASED`、`STEP_CONFIRMED` |
| 事件 payload 模型 | `<事件主体><动作>Payload` | `RunFailedPayload`、`LeaseReclaimedPayload` |
| 错误码 | `LF-<DOMAIN>-<4位数字>` | `LF-LEASE-0503` |
| 异常类 | `<语义>Error` | `FencedOutError`、`BudgetExhaustedError` |
| ORM 模型 | 单数 `PascalCase`；表名复数 `snake_case` | `Task` → `tasks` |
| DTO | `<资源><用途>` | `TaskCreate` / `TaskRead` / `TaskUpdate` / `TaskDetail` / `TaskQuery` |
| 仓储 | `<模型>Repository` | `TaskRepository` |
| 模块 | `snake_case`，仓储文件 `<领域>_repo.py` | `lease_repo.py` |
| 私有成员 | 单下划线前缀 | `self._clock` |

---

## 4. 类型注解

- **所有**函数签名必须有完整注解，包括 `-> None`。
- 用 `from __future__ import annotations`，类型写 `X | None` 而非 `Optional[X]`。
- 禁止裸 `Any`；确需时加注释说明。
- ORM 用 `Mapped[T]` + `mapped_column()`，不用旧式 `Column()`。
- 协议优先于 ABC：`Clock`、`VectorBackend` 等扩展点用 `typing.Protocol`。

```python
from __future__ import annotations

async def acquire(
    self,
    *,
    task_id: str,
    worker_id: str,
    ttl_s: int = 300,
    now: datetime | None = None,
) -> Lease | None:
    """获取任务租约。

    Args:
        task_id: 目标任务 ULID。
        worker_id: 申请者 Worker ULID。
        ttl_s: 租约有效期（秒）。
        now: 注入的当前时间；为 None 时由内部 Clock 提供。

    Returns:
        成功返回 Lease，被别人抢先返回 None（不抛异常）。

    Raises:
        TaskNotFoundError: task_id 不存在。
    """
```

---

## 5. 函数签名约定

- **两个以上参数一律 keyword-only**（`*` 分隔）。这是本项目踩过的坑：位置参数在跨 WP 调用时极易错位，且新增参数会静默破坏调用方。
- 布尔参数必须 keyword-only，禁止 `f(True)` 这种调用。
- 所有可选参数必须有默认值。
- 返回「失败」用返回值（`None` / `CasResult(ok=False)`），返回「异常情况」才抛异常。竞争失败**不是**异常。

---

## 6. 文档字符串

- 所有公开类、公开方法必须有 docstring，Google 风格。
- 中文撰写，术语（`fence token`、`CAS`、`lease`）保留英文原文。
- 复杂算法（哈希链、BM25 打分、CAS 重试）在函数内加行内注释解释**为什么**，不解释**是什么**。

---

## 7. 导入

顺序（ruff `isort` 自动整理）：标准库 → 第三方 → `loopforge` 本包 → 相对导入。

- **禁止循环导入**。`core` 不得 import `db`；`db` 不得 import 业务包；`schemas` 不得 import `models`。
- 禁止 `from x import *`。
- 每个包的 `__init__.py` 必须显式声明 `__all__`。
- 跨 WP 引用**只能**从包顶层导入（`from loopforge.schemas import TaskRead`），不得深入内部模块路径。

---

## 8. 测试

- 目录镜像源码：`tests/unit/<包>/test_<模块>.py`。
- `asyncio_mode = "auto"`，async 测试不需要 `@pytest.mark.asyncio`。
- 数据库测试用 `tests/fixtures/db.py` 的 `uow` / `second_uow` fixture（内存库 + `StaticPool`）。
- 时间敏感测试必须注入 `FrozenClock`，禁止 `sleep` 等待。
- 测试名说清**行为与期望**：`test_task_cas_1000_contenders_exactly_one_wins`，不要 `test_cas_2`。
- 断言要具体：`assert result.ok is False`，不要 `assert not result.ok`。
- 每个 bug 修复必须附一条**先复现失败**的回归测试。

---

## 9. 提交信息

Conventional Commits：

```
<type>(<scope>): <subject>

<body>

Refs: WP<n>
```

`type` ∈ `feat|fix|docs|refactor|test|chore|perf`；`scope` 用包名（`core`/`db`/`models`/`schemas`/`alembic`）。
subject 中文，祈使句，不超过 50 字，句末不加句号。
