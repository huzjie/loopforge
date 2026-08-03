version: v1
# 多源演化归一化

把来自不同来源的原始演化候选（社区实践、用户反馈、验证失败、指标异常）
归一化成统一的工作项。**保留原始信息，不臆造。**

## 输入

- 来源：{{ source }}
- 原始内容：{{ raw }}

## 输出（严格 JSON）

```json
{
  "title": "≤ 80 字的工作项标题",
  "body": "归一化正文",
  "tags": ["tag1", "tag2"],
  "severity": "INFO|NOTICE|WARNING|ERROR|CRITICAL",
  "evidence": "原始证据摘要"
}
```

## 铁律

1. `title` 要可独立成立：脱离原始上下文也能看懂。
2. `tags` 从正文提炼，用于聚类与打分。
3. `severity` 来自原始来源；来源没给时默认 `INFO`。
4. 只输出 JSON。
