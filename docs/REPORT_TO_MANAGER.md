# 给主理人齐活林的交付回报（架构设计 + 任务分解）

> 配套产出：`docs/ARCHITECTURE.md`（3249 行，怎么建）、`docs/TASK_BREAKDOWN.md`（1607 行，谁先干）、`class-diagram.mermaid`、`sequence-diagram.mermaid`。本文为摘要，≤1000 字。

## 一、架构要点
LoopForge 采用**五层架构**：接口层(FastAPI+OpenAPI3.1)／编排层(LoopEngine+Worker+租约看门狗)／执行层(Executor 沙箱+工具+验证闭环)／治理层(四层记忆+策略引擎+Budget 守卫)／基础设施层(DB/Alembic/事件溯源/可观测)。核心原则「**抽象在边界、实现在叶子**」：`BaseProvider`/`BaseExecutor`/`BaseTool`/`BaseRetriever`/`BaseRole` 五抽象，新增能力=新文件+注册，不改核心。

三大热点难点解法已定稿：① **幂等性**——两阶段意图协议 + 副作用四级分类(PURE/IDEMPOTENT/PROBEABLE/UNSAFE) + Probe 对账，崩溃可重放零重复；② **信息保真度**——L0~L4 四层记忆 + L0 钉死约束永压缩 + 三级压缩 + Lineage 谱系；③ **租约并发**——条件 UPDATE 乐观锁 + Fencing Token + reclaim/attempt 双计数解耦（回收与失败分离，避免误杀在途任务）。另含 26 张表、141 REST 端点、62 种事件枚举、17 域错误码(`LF-<DOMAIN>-<NNNN>`)。`MockProvider` 三件套保证**零 Key 可跑**；`scripts/verify_scale.py` 在 CI 扫描空壳/TODO/pass，杜绝空文件交付。

## 二、文件数统计（合计 611，全达标）
| 类别 | 文件数 | 下限 | 达标 |
|---|---:|---:|:--:|
| Python 源码 | 312（含 alembic 7） | ≥190 | ✅ +64% |
| 前端源码 | 87 | ≥55 | ✅ +58% |
| 测试 | 64 | ≥45 | ✅ +42% |
| 文档 | 58 | ≥28 | ✅ +107% |
| 配置/部署 | 90 | ≥25 | ✅ +260% |
| **合计** | **611** | ≥320 | ✅ +91% |

## 三、8 个工作包（WP）依赖与波次
- **W0**：WP1 内核与数据底座(99) —— 独占，出口闸门 **G1**。所有公共词汇表（枚举/错误码/事件/ORM/迁移/依赖清单）一次性写满、冻结、只读。
- **W1**：WP2 Provider(30) ∥ WP3 执行沙箱工具(44) ∥ WP5 记忆治理(61) —— 闸门 **G2**，三者仅硬依赖 WP1。
- **W2**：WP4 编排引擎(49) ∥ WP6 角色VCS演化(48) ∥ WP7 API/CLI/可观测(99) ∥ WP8 前端骨架(176) —— 闸门 **G3**。WP4/6/7 以**契约**依赖 WP2/3/5（依赖抽象签名而非实现）。
- **W3**：WP8 收口 + 全量 E2E + 部署 + 文档 —— 闸门 **G4**。WP8 硬依赖 WP7。
- 关键设计：除 WP1 外**无超过 2 跳的硬依赖链**，最大程度并行。

## 四、三个最易并行冲突点及规避
1. **公共枚举/常量文件（最高危）**：`EventType`(62)、17 域错误码等若多人改必冲突。→ WP1 在 W0 一次写满写死，转只读并加 `CODEOWNERS`；新增只能段尾追加、单点合入；CI 校验 EventType 成员数 == payload map 键数。
2. **ORM 模型与 Alembic 迁移链**：26 表由 WP1 在 `0001~0006` 一次建完，其他 WP 禁新增迁移、禁改 `models/`。确需加列走变更流程，由 WP1 owner 串行追加（禁 `--autogenerate`，手工指定 down_revision）；CI 校验 `alembic heads` 恰一行 + 升/降/升三连通过。
3. **注册表 `__init__.py` 相邻行冲突**：改为「约定式自动发现」`Registry.autoload(package, base_class)`，各 `__init__` 只留一行且永不再改；新增组件=只新建自身文件、零公共改动。API 路由遍历模块自动注册，保证 OpenAPI 顺序稳定。

> 结论：两份主文档与图件已落盘自洽（数字经 `verify_scale.py` 口径对齐），8 WP 可即日按 W0→W1→W2→W3 四波次 + G1~G4 四闸门并行启动，三条铁律(R1 签名冻结/R2 不越界/R3 禁空壳)保底不打架。
