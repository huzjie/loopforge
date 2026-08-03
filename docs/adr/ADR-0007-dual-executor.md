# ADR-0007：双执行器策略（Local + Docker）

- **状态**：已接受（W1 · WP3）
- **日期**：2026-08-03
- **决策者**：架构师 + WP3（Execution）

---

## 背景

LoopForge 需要「Agent 的手」——受策略约束的文件系统、Shell、Git、测试执行能力。
执行后端的选型直接决定：**开箱可用性**（自托管用户不一定有 Docker）与
**安全隔离等级**（本地子进程共享宿主内核）。

## 决策

**双实现、同一契约、自动降级**：

1. `LocalSubprocessExecutor`（`kind="local_subprocess"`）—— **默认**。asyncio
   subprocess + 路径沙箱 + 命令策略 + 资源限额 + 进程组整杀，**Windows / Linux /
   macOS 全平台可跑**，是唯一保证开箱即用的实现。
2. `DockerExecutor`（`kind="docker"`）—— **生产实现**。容器隔离、只读挂载、
   默认 `--network none`、cgroup 限额、流式输出。Docker SDK 缺失 / daemon 不可达
   时抛 `LF-EXECUTOR-0804`（`ContainerUnavailableError`），**绝不直接崩栈**，
   下游据此降级到本地执行器。
3. `K8sJobExecutor`（`kind="k8s"`）—— **v1.0 不启用**，只留接口骨架
   （`LF-CORE-0005`），防止下游拿到一个静默降级的假实现。

两者通过**同一套 `BaseExecutor` 契约测试**参数化跑两遍验收（§11.4 冻结签名）。

## 理由

- **可用性优先**：自托管场景 Docker 不一定可用；本地执行器保证零依赖开箱即用。
- **安全兜底**：生产部署要求物理隔离；Docker 提供容器 + cgroup + 网络隔离。
- **降级语义明确**：容器不可用是**清晰错误码**而不是静默回退，让编排层能感知
  并选择降级策略，而不是在错误的时间点炸出随机异常。
- **单一契约**：下游（WP4）只依赖 `BaseExecutor` 七个方法，不感知后端差异。

## 后果

- 正向：默认零依赖可用；生产可一键切 Docker；K8s 接口冻结为后续版本铺路。
- 负向：本地执行器在 Windows 上没有进程级内存硬限额（`memory_enforced=False`），
  大内存作业需切 Docker。
- 风险：环境变量白名单在 Windows 上曾导致子进程 Winsock 初始化失败
  （WinError 10106），已在 `LocalSubprocessExecutor._build_env` 中强制保留
  `SystemRoot` 等关键变量并做了回归测试。

## 替代方案

- **只用 Docker**：被否决 —— 自托管门槛过高，CI 环境没有 daemon。
- **只用本地**：被否决 —— 生产隔离等级不足。
- **单实现 + 运行时探测隔离**：被否决 —— 复杂度高于显式双实现。
