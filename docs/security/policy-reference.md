# 策略参考（policy-reference）

> 治理层安全策略的完整参考：DSL 语法、规则分类、判定语义、内置策略、
> 红线用例集。对应 `src/loopforge/governance/policy/` 与 `config/policies/`。

## 1. 判定语义

- 规则按 `order` 升序求值；`deny > require_approval > allow` 严格短路
  （G1：先命中 deny 立即返回）。
- 未命中任何规则 → **默认拒绝**（`R-DEFAULT-DENY`）。
- 命令类规则**正则 + AST 双重判定**（G2）：正则防「已知模式」，
  AST 层（`ast_analyzer.py`）用 shlex 拆词识别 base64/hex 解码执行、
  `curl|sh`、进程替换等绕过。

## 2. DSL 语法

```yaml
name: default
version: 1
description: 默认安全基线
rules:
  - rule_id: R-SHELL-001
    category: shell        # shell|path|network|git|secret|dependency|tool|default
    match: {}              # 空 = 该分类的通用判定（如 shell 危险命令库）
    effect: DENY           # DENY | REQUIRE_APPROVAL | ALLOW
    order: 10
    reason: 危险命令
    severity: CRITICAL
```

`match` 字段按分类不同：

| 分类 | 常用 match 键 |
|---|---|
| shell/command | `pattern`（额外正则）；空 = 命中内置危险命令库 |
| path | `deny` / `allow`（glob 列表）、`sandbox_root` |
| network | `allow_domains` / `deny_domains` / `allow_private` |
| git | `protected_branches` |
| secret | 空 = 内置密钥正则库 |
| dependency | `deny_names` / `deny_version_patterns` |
| tool | `tool_name` / `tool_names` |
| default | 空（兜底） |

## 3. 内置策略（config/policies/）

| 文件 | 定位 | 关键差异 |
|---|---|---|
| `default.yaml` | 默认安全基线 | 危险命令拒绝；命令替换审批；只读工具放行；白名单域放行 |
| `strict.yaml` | 严格模式 | 一切 shell 拒绝；路径限工作区；网络仅白名单 |
| `permissive.yaml` | 实验沙箱 | 危险命令仍拒绝；私网允许；常用工具放行 |

## 4. 红线用例集（config/redlines.yaml）

225 条用例，覆盖：删除/格式化/提权/反弹 shell/base64 解码/下载执行/
git 强推/密钥泄露/私网 SSRF/危险依赖等。自测：

```bash
.venv/Scripts/python.exe -m pytest tests/unit/governance/test_redlines.py -q
# 期望：召回率 1.0，误报率 0.0
```

## 5. 引擎入口

```python
from loopforge.governance import PolicyEngine, PolicyAction

engine = PolicyEngine(rules=PolicyLoader().load("default"))  # 内存规则
# 或 engine = PolicyEngine(session_factory=factory)  # 从 policies 表加载生效策略

verdict = await engine.evaluate(PolicyAction(
    kind="command", tool_name="run_shell", argv=["rm", "-rf", "/"],
))
assert verdict.decision == PolicyDecision.DENY
```
