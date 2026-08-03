version: v1
# PR 描述生成

为一次改动生成 Pull Request 描述：**理由 + 自测报告 + 轨迹链接**。

## 输入

- 改动摘要：{{ change_summary }}
- 文件清单：{{ files }}
- 测试报告：{{ test_report }}
- 轨迹链接：{{ trace_link }}

## 输出（严格 JSON）

```json
{
  "title": "PR 标题",
  "body": "## 理由\n...\n\n## 自测报告\n...\n\n## 轨迹\n...",
  "labels": ["loopforge:auto"]
}
```

## 铁律

1. body 必须包含三段：理由、自测报告、轨迹链接。
2. 测试失败时必须如实写明失败项，不得只写「测试通过」。
3. 只输出 JSON。
