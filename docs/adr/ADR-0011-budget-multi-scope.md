# ADR-0011：多级预算作用域（budget-multi-scope）

- 状态：已接受
- 日期：2026-08-03
- 关联：ARCHITECTURE §10.2、TASK_BREAKDOWN §7.1.2、PRD 预算熔断

## 背景

16 天无人值守运行必须「不会把钱烧光」。单层预算（只按 Run）无法
防止「多个 Run 同时把日额度烧穿」或「组织级失控」。

## 决策

1. **三级作用域**（`BudgetScope`）：`RUN`（单 Run 上限）、`DAY`
   （按 UTC 日期）、`ORG`（组织级上限）。一次 LLM 调用同时检查三层，
   任一触发即拦截。
2. **三级动作**（`BudgetAction`）：`WARN → THROTTLE → HALT`，
   阈值按使用率比例（`warn_at < throttle_at <= halt_at <= 1.0`）。
3. **预留-确认两阶段**：
   - `BudgetGuard.precheck()` —— LLM 调用**前**：原子预留预估额度，
     失败即 HALT（Run 状态流转 `HALTED`）；
   - `BudgetGuard.record()` —— LLM 调用**后**：预留换实际 + 写
     `cost_records` + 触发事件。
4. **防并发超支**：
   - 进程内：per-budget asyncio.Lock 串行化同一预算的预留/确认；
   - DB 层：无条件原子累加 + 超限补偿回滚（SQLite 写锁保证写者串行），
     并发下最终消耗 ≤ 上限。
5. 金额一律 `Decimal`，禁止 float。

## 理由

- 多级 scope 防止「单 Run 合规、日账单爆表」；
- 预留-确认两阶段让「预检放行 ≠ 一定会花」的窗口期也有保护；
- 不引入 Redis 锁：SQLite 写锁 + 进程锁足够单机/自托管场景。

## 替代方案

- 单层预算：实现简单，但无法控制跨 Run 风险，否决。
- Redis 分布式锁：引入外部依赖，与自托管定位冲突。

## 后果

- 正向：并发 10 个预检（上限 5.0）恰好 5 个放行，最终消耗 ≤ 5.0；
- 负向：预留若未确认会占用额度，提供 `release()` 供 LLM 失败时回滚。

## 测试

`tests/unit/governance/test_budget_guard.py`：阈值触发、HALT 停住、
并发不超支、release 回滚。
