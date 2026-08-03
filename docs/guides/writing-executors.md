# 如何编写一个 Executor

- **适用对象**：LoopForge 贡献者（需要新执行后端时）
- **状态**：WP3 交付（W1）
- **约定**：`TASK_BREAKDOWN.md` §11.4（`BaseExecutor` 契约冻结）、`ARCHITECTURE.md` §7.2

---

## 1. 契约一览（冻结签名，一个字符都不能改）

```python
class BaseExecutor(abc.ABC):
    kind: ClassVar[str]

    async def prepare(self, spec: WorkspaceSpec) -> WorkspaceHandle: ...
    async def run(self, cmd: CommandSpec) -> CommandResult: ...
    async def write_file(self, handle, path, content, *, encoding="utf-8") -> FileStat: ...
    async def read_file(self, handle, path, *, max_bytes=1_048_576) -> FileContent: ...
    async def list_dir(self, handle, path, *, depth=1) -> DirListing: ...
    async def cleanup(self, handle) -> None: ...
    async def healthcheck(self) -> ExecutorHealth: ...
```

关键约定：

1. **`argv: list[str]`，全仓禁止 `shell=True`** —— 命令注入的根因是把字符串交给
   shell 解释，argv 数组从源头消灭这一类攻击面。
2. **非零退出码不抛异常**，由调用方判断 `CommandResult.ok`。
3. **输出必须截断且可追溯**：超过 `max_output_bytes` 时置 `truncated=True`。
4. **时间只走 `core.clock.Clock`**，禁止 `datetime.now()`。
5. `healthcheck()` **任何情况下都不得抛异常**，用 `healthy=False` 表达故障。

## 2. 最小实现骨架

```python
# src/loopforge/executors/my_executor.py
from typing import ClassVar
from loopforge.executors.base import (
    BaseExecutor, WorkspaceSpec, WorkspaceHandle, CommandSpec, CommandResult,
    FileStat, FileContent, DirListing, ExecutorHealth,
)

class MyExecutor(BaseExecutor):
    kind: ClassVar[str] = "my_executor"
    enabled: ClassVar[bool] = True

    async def prepare(self, spec: WorkspaceSpec) -> WorkspaceHandle: ...
    async def run(self, cmd: CommandSpec) -> CommandResult: ...
    async def write_file(self, handle, path, content, *, encoding="utf-8") -> FileStat: ...
    async def read_file(self, handle, path, *, max_bytes=1_048_576) -> FileContent: ...
    async def list_dir(self, handle, path, *, depth=1) -> DirListing: ...
    async def cleanup(self, handle) -> None: ...
    async def healthcheck(self) -> ExecutorHealth: ...
```

**不要**在子类里新增抽象方法 —— 那会让下游同时依赖两套契约，跨包升级立刻失控。

## 3. 复用已有的安全地基（强烈建议）

执行层四道防线已经写好，新执行器应尽量复用：

| 模块 | 职责 | 用法 |
|---|---|---|
| `executors/path_sandbox.py` | 路径钉死 | `PathSandbox(root).resolve(rel)` |
| `executors/command_policy.py` + `policy_guard.py` | 命令策略 | `CommandPolicyGuard().enforce(argv, context=...)` |
| `executors/resource_limits.py` | 超时 / 输出 / 进程组 | `spawn_kwargs(limits)` + `kill_process_tree(proc)` |
| `executors/workspace.py` | 目录隔离 / 配额 / 快照 | `WorkspaceManager(root).create(spec)` |
| `executors/stream.py` | 流式输出捕获 | `StreamSink` + `pump_pair` |

以 `LocalSubprocessExecutor`（`executors/local.py`）为参考实现：
它演示了「策略 → 沙箱 → 派生 → 泵取 → 整组杀死」的完整链路，并且是**当前
Windows 环境实测可跑**的默认实现。

## 4. 注册与工厂

`executors/__init__.py` 提供 `create_executor(kind)` 与 `ExecutorPool`。
新增 kind 时在 `create_executor` 里加一个分支即可；未启用的 kind 抛
`ExecutorNotEnabledError`（参考 `K8sJobExecutor`，v1.0 唯一允许「不实现」的骨架）。

## 5. 验收

- 同一套契约测试参数化跑两遍（Local + 你的实现）。
- `LocalSubprocessExecutor` 超时后**进程组被完整杀死**，无孤儿进程（POSIX + Windows 各验证）。
- Docker 相关测试用 `@pytest.mark.skipif` 自动跳过，**测试不得依赖 Docker**。
