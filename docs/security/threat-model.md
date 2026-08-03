# 威胁模型（threat-model）

> 治理层（WP5）面对的主要威胁与缓解措施。范围：上下文治理、预算熔断、
> 安全策略引擎、人工审批、审计链。

## 1. 威胁清单

| # | 威胁 | 后果 | 缓解 | 对应模块 |
|---|---|---|---|---|
| T1 | 上下文爆炸（第 300 轮忘记第 20 轮约束） | 行为漂移、违反约束 | Pinned Constraints 强制注入 + 保真度校验 | `memory/pinned.py`、`check_fidelity` |
| T2 | LLM 上下文超窗口 | 调用失败、费用暴涨 | 三级压缩 + 层配额 + 预算熔断 | `memory/compressor.py`、`budget/guard.py` |
| T3 | 危险命令（`rm -rf /`、`curl|sh`） | 数据毁灭、供应链投毒 | 命令正则 + AST 双重判定，225 条红线用例召回 100% | `policy/rules/command.py`、`ast_analyzer.py` |
| T4 | 命令混淆绕过（base64/hex/`$(...)`） | 绕过正则黑名单 | AST 层解码识别 + 命令替换审批 | `ast_analyzer.py` |
| T5 | 路径逃逸 / SSRF | 读取敏感文件、访问内网 | 路径沙箱（glob + 目录穿越检测）+ 私网地址拒绝 | `policy/rules/path.py`、`network.py` |
| T6 | 密钥泄露（API Key 写进代码） | 账号被盗 | 密钥正则库扫描，命中即拒 | `policy/rules/secret.py` |
| T7 | 预算超支（并发 LLM 调用） | 烧钱失控 | 预检预留 + 原子扣减 + 补偿回滚，并发下 100% 不超支 | `budget/guard.py` |
| T8 | 审批形同虚设（超时默认放行） | 高危操作无人把关 | 超时默认拒绝（`deny`），`allow` 需显式配置并写明理由 | `approval/gate.py` |
| T9 | 审计日志被篡改 | 无法追责 | 哈希链（`sha256(prev + canonical_json(payload))`），断链即告警，不得静默修复 | `audit/chain.py` |
| T10 | git 历史被改写 / 受保护分支被写 | 供应链不可信 | 受保护分支拒绝 + 强推审批 | `policy/rules/git.py` |
| T11 | 依赖 typosquatting | 恶意代码执行 | 高危包名库 + 危险版本检测 | `policy/rules/dependency.py` |

## 2. 信任边界

```
用户/API → [审批闸门] → 策略引擎 → [预算预检] → LLM → [预算确认] → 审计链
              ↑                    ↑
          高危操作              消耗 token
```

- **策略引擎**在 LLM 调用前判定（G3 前置于 `precheck`）。
- **预算预留**在 LLM 调用前原子完成，**确认扣减**在调用后完成，同一事务。
- **审计链**全程记录：策略判定、审批、预算事件、约束违规。

## 3. 失效模式

| 失效 | 处理 |
|---|---|
| EmbeddingRetriever 不可用 | 优雅降级到 BM25（默认关闭） |
| LLM 调用失败（预留未确认） | `BudgetGuard.release()` 回滚预留 |
| Summarizer LLM 不可用 | 确定性抽取兜底，不阻断压缩 |
| 审计链断链 | `ChainVerifyReport` 精确定位损坏 seq，发告警，不静默修复 |

## 4. 测试覆盖

- `test_policy_engine.py`：31 种攻击命令全拦截 + 短路顺序 + 默认拒绝
- `test_redlines.py`：225 条红线召回 100%、误报 0%
- `test_budget_guard.py`：并发 10 预检 5 放行，最终消耗 ≤ 上限
- `test_audit_chain.py`：1 万条链完整；篡改中间一条精确定位
- `test_pinned.py`：连续 500 轮压缩后 Pinned 约束 100% 无损
