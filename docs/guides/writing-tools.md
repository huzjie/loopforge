# 如何编写一个 Tool

- **适用对象**：LoopForge 贡献者、第三方插件作者
- **状态**：WP3 交付（W1）
- **约定**：`TASK_BREAKDOWN.md` §11.5（`BaseTool` 契约冻结）、§13.3（新增 = 只新建文件，零公共文件改动）

---

## 1. 核心规则：side_effect_class 必须声明

每个工具必须显式声明 `side_effect_class`（四选一）。这个字段直接决定崩溃恢复时
WP4 是**重放**还是**重规划**（`ARCHITECTURE.md` §8.1.4 决策表）：

| 值 | 语义 | 恢复行为 | 内置示例 |
|---|---|---|---|
| `PURE` | 无副作用，可无条件重放 | 直接重放 | `read_file` `list_dir` `search_code` `run_tests` |
| `IDEMPOTENT` | 重放结果一致 | 重放（probe 比对 sha256） | `write_file` `edit_file` `apply_patch` |
| `PROBEABLE` | 有副作用但可探测 | probe 确认后定夺 | `git_ops` `create_pr` `http_fetch` `ask_human` |
| `UNSAFE` | 有副作用且不可探测 | 一律 replan | `run_shell` |

**漏声明 = 恢复逻辑失效**。`BaseTool` 的元类会在导入时直接报错，CI 也会拦截。

## 2. 最小实现（三步）

```python
# src/loopforge/tools/my_tool.py
from typing import ClassVar
from pydantic import BaseModel, Field
from loopforge.core.enums import SideEffectClass
from loopforge.tools.base import BaseTool, ToolContext, ToolResult, ProbeResult, StepIntent


class MyToolArgs(BaseModel):
    path: str = Field(min_length=1, description="相对工作区根的文件路径")


class MyTool(BaseTool):
    name: ClassVar[str] = "my_tool"                 # 全仓唯一，冻结
    description: ClassVar[str] = "做什么、怎么用"
    args_schema: ClassVar[type[BaseModel]] = MyToolArgs
    side_effect_class: ClassVar[SideEffectClass] = SideEffectClass.PURE

    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        parsed = args.model_dump()
        if ctx.executor is None or ctx.workspace is None:
            return ToolResult.failure("executor / workspace 未注入")
        try:
            content = await ctx.executor.read_file(ctx.workspace, parsed["path"])
        except Exception as exc:
            return ToolResult.failure(f"读取失败：{exc}", error_code=getattr(exc, "code", "LF-TOOL-0902"))
        return ToolResult(ok=True, output=content.content, data={"size": content.size_bytes})

    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        # PURE 工具无需探测
        return ProbeResult(executed=False, confidence="high", evidence="PURE，无需探测")
```

**零注册成本**：`tools/__init__.py` 只调 `registry.autoload_builtins()`，
新工具 = 只新建自己的文件，`__init__.py` 永不改动（§13.3 冲突规避）。

## 3. 三类工具的实现要点

### 3.1 `PURE` 只读工具

- `probe()` 返回 `executed=False, confidence="high"` 即可。
- 不得调用任何有副作用的执行器方法。

### 3.2 `IDEMPOTENT` 写工具（`write_file` / `edit_file` / `apply_patch`）

- `probe()` 必须**比对目标文件 sha256 与意图中的期望值**：

```python
async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
    stat = await ctx.executor.stat(ctx.workspace, intent.args["path"])
    expected = intent.expected_sha256 or sha256_hex(str(intent.args.get("content", "")).encode())
    executed = stat.exists and stat.sha256 == expected
    return ProbeResult(executed=executed, external_ref=stat.sha256,
                       confidence="high" if executed else "medium", evidence=...)
```

### 3.3 `PROBEABLE` 工具（`git_ops` / `create_pr` / `http_fetch` / `ask_human`）

probe 依据（§8.1.5 强制约定）：

| 工具 | probe 依据 |
|---|---|
| `git_ops:commit` | `git log --all --grep=<idempotency_key>`，commit message 必须带 `LoopForge-Idempotency-Key` trailer |
| `git_ops:branch` | `git rev-parse --verify <branch>` |
| `create_pr` | 本地 PR 草稿 / `gh pr view`，body 必须含 `<!-- lf-step-key:<key> -->` |
| `http_fetch` | 带 `Idempotency-Key` 时的响应缓存回查 |
| `ask_human` | approvals 表 / 本地审批文件 |

## 4. 错误处理规范

- `execute()` **不抛裸异常**：一切失败返回 `ToolResult(ok=False, error_code=...)`。
- 错误码：参数非法 `LF-TOOL-0901`、执行失败 `LF-TOOL-0902`、未注册 `LF-TOOL-0903`、
  角色越权 `LF-TOOL-0904`、需确认 `LF-TOOL-0905`。
- 输出必须截断：单次输出不超过 `loopforge.constants.MAX_TOOL_OUTPUT_BYTES`（1 MiB），
  截断时置 `truncated=True` —— 禁止把 10MB 日志塞进 LLM 上下文。

## 5. 测试要求

每个工具至少覆盖：正常路径、失败路径、`probe()` 命中 / 未命中。
参考 `tests/unit/tools/test_fs_tools.py`、`test_patch_tools.py`、`test_shell_git_tools.py`。
