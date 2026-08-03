version: v1
# 验证失败 → 修复任务

把一次验证失败（编译 / 断言 / 超时 / 环境 / 依赖）转化为可执行的修复任务。
**失败归因必须具体，禁止「系统异常」这类空话。**

## 输入

- 失败信息：{{ failure }}
- 失败分类：{{ category }}
- 相关任务：{{ task_title }}

## 输出（严格 JSON）

```json
{
  "task_draft": {
    "task_key": "T-FIX-0001",
    "title": "修复标题",
    "body": "修复正文（含复现步骤与验收标准）",
    "kind": "fix",
    "priority": 100,
    "depends_on": [],
    "touch_paths": ["src/xxx.py"],
    "acceptance": ["验收标准"]
  },
  "root_cause_hypothesis": "根因假设",
  "rerun_command": "复现命令"
}
```

## 铁律

1. `touch_paths` 必须包含失败涉及的源码路径。
2. 根因假设要给出可验证的检查步骤。
3. 只输出 JSON。
