version: v1
# 约束保真度校验

对照 Pinned Constraints 逐条校验一段计划 / 代码 / 摘要是否仍满足约束。
**逐条原文比对，不得改写约束。**

## 输入

- 待校验文本：{{ plan_text }}
- 已记录约束：{{ constraints }}

## 输出（严格 JSON）

```json
{
  "satisfied": true,
  "checked": [
    {
      "constraint": "约束原文",
      "ok": true,
      "evidence": "满足的证据"
    }
  ],
  "violations": [
    {
      "constraint": "约束原文",
      "evidence": "违反的证据"
    }
  ]
}
```

## 铁律

1. `constraint` 字段必须是**逐字原文**，一个字符都不能改。
2. 证据缺失按违反处理（无法证明满足 = 不满足）。
3. 只输出 JSON。
