# 执行沙箱安全模型

- **适用对象**：安全评审、运维、贡献者
- **状态**：WP3 交付（W1）
- **威胁模型**：LLM 生成的路径 / 命令是**不可信输入**；插件可绕过工具层，但**执行器是唯一的物理出口（choke point）**。

---

## 1. 四道防线（自底向上）

```
┌─────────────────────────────────────────────────────────┐
│ 4. 工作区隔离  workspace.py：run/task 级目录 + 配额 + 快照 │
│ 3. 命令策略    command_policy + policy_guard：白/黑名单   │
│ 2. 资源限额    resource_limits：超时 / 输出 / 进程组整杀   │
│ 1. 路径沙箱    path_sandbox：realpath + 前缀白名单        │
└─────────────────────────────────────────────────────────┘
```

**核心不变量**：`normcase(realpath(result))` 必须以 `normcase(realpath(root))`
为前缀。realpath 会解析符号链接，所以「workspace 内的软链指向外部」也会被
前缀比较兜住 —— 这是防 symlink 逃逸的根本原因。

## 2. 路径沙箱拦截的攻击面（测试见 `test_path_sandbox.py`，≥20 种）

| 族 | 样例 | 拦截 |
|---|---|---|
| 相对穿越 | `../../etc/passwd`、`sub/../../../` | ✅ |
| 绝对路径 | `/etc/shadow`、`C:\Windows\System32\SAM` | ✅ |
| 盘符相对 | `C:foo` | ✅ |
| UNC / 设备路径 | `\\server\share`、`\\?\C:\`、`\\.\PhysicalDrive0` | ✅ |
| 家目录展开 | `~/.ssh/id_rsa`、`~root/.bashrc` | ✅ |
| URL 编码（含双重） | `%2e%2e%2f`、`%252e%252e` | ✅ |
| 符号链接 | 工作区内 `link -> /etc` | ✅（默认禁软链） |
| NUL 截断 | `ok.txt\x00/../../etc/passwd` | ✅ |
| Windows 保留设备名 | `CON`、`NUL`、`COM1` | ✅ |
| NTFS 备用数据流 | `file.txt:secret` | ✅ |
| 超长 / 超深 | 4096 字符 / 64 层 | ✅ |

## 3. 命令策略拦截的攻击面（测试见 `test_command_policy.py`）

- **破坏性删除**：`rm -rf /`、`rm -rf ~`、`rm --force -r /`（任意顺序与长选项）、
  Windows `del /s C:\`、PowerShell `Remove-Item -Recurse`。
- **磁盘 / 文件系统**：`mkfs`、`format`、`diskpart`、`dd of=/dev/sd*`。
- **远程代码执行**：`curl | sh`、PowerShell `IEX`、certutil 下载、nc 反弹 shell。
- **提权 / 账户**：`sudo`、`su root`、`net user /add`。
- **敏感读取**：`/etc/shadow`、`~/.ssh/id_rsa`、`~/.aws/credentials`、SAM hive。
- **持久化 / 系统**：`crontab`、`schtasks`、`reg add`、`shutdown`、`systemctl`。
- **参数注入**：`bash -c "<payload>"` 一族做全文二次扫描（`SHELL_BINARIES` 白名单 +
  `SHELL_METACHARACTERS` 触发）。

**双层策略，缺一不可**：工具层过 `PolicyEngine`（或 `CommandPolicyGuard.evaluate_action`），
执行器层再过一次 `CommandPolicyGuard.enforce` —— 插件绕过了工具层也绕不过执行层。

**命中即写审计**：任何非 `ALLOW` 裁决写 `audit_logs` 表（`DbAuditSink`），
审计失败降级为日志，不阻塞执行。

## 4. 资源限额

- **超时**：墙钟超时后**整组杀死** —— POSIX `setsid` + `killpg(SIGKILL)`；
  Windows `CREATE_NEW_PROCESS_GROUP` + `taskkill /F /T`。`pytest` 会 fork 子进程、
  `npm test` 会拉起守护进程，只 kill 直接子进程会留下一地孤儿占住文件句柄。
- **输出**：`OutputAccumulator` 带上限累加，超限后**继续消费但不保留**
  （否则子进程卡在 write 上永不退出，超时逻辑失效）。
- **内存**：POSIX `setrlimit(RLIMIT_AS)`；Windows 无简易 per-process 接口，
  `ResourceLimits.memory_enforced` 如实标注。

## 5. 环境变量白名单

`cmd.env` 与宿主 `os.environ` 都与白名单求交集，未列出的键一律不传给子进程。

**Windows 兼容铁律**：`PATH` / `SYSTEMROOT`（大小写变体）/ `COMSPEC` / `TEMP` / `TMP`
**恒保留** —— 实测发现一旦 `SystemRoot` 被过滤，子进程加载 `_overlapped`/Winsock
直接报 `WinError 10106`，连 `import asyncio` 都失败。

## 6. 已知边界（v1.0 接受）

- Windows 无进程级内存硬限额，靠 Docker 执行器补齐。
- 本地子进程共享宿主内核：生产环境建议 `executor.kind = "docker"`（容器 + 只读挂载 +
  默认 `--network none` + cgroup 限额）。
- K8s Job 执行器 v1.0 未启用（`LF-CORE-0005`），接口骨架见 `executors/k8s.py`。
