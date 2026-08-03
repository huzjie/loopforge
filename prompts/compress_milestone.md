version: v1
# 里程碑递归压缩

把一段长程运行的记忆内容压缩成里程碑摘要。**保留事实，丢弃冗余。**

## 输入

- 里程碑：{{ milestone }}
- 待压缩内容：{{ content }}

## 输出（严格 JSON）

```json
{
  "summary": "不超过 200 字的摘要",
  "key_facts": ["必须保留的事实清单"],
  "info_loss_score": 0.0,
  "next_level_hint": "给下一级压缩的提示"
}
```

## 铁律

1. `info_loss_score` 在 [0,1]：数字越大丢得越多，超过 0.4 必须保留更多原文。
2. 文件路径、commit sha、数字指标属于 key_facts，不得省略。
3. 只输出 JSON。
