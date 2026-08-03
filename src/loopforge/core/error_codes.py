"""错误码体系 ``LF-<DOMAIN>-<NNNN>``（**已冻结**）。

来源：``docs/ARCHITECTURE.md`` §10.2 / ``docs/TASK_BREAKDOWN.md`` §12.1。

设计要点
--------
1. 每个错误码是一个 ``(code, http_status, default_message_zh, retryable)`` 四元组，
   集中登记在 :data:`ERROR_REGISTRY`。
2. 域段一旦分配即冻结：**不得改变已分配码的含义，不得新增域段**。
   新增码只能在既有域的空闲号段内追加。
3. HTTP 状态码由 :func:`code_to_http` 单点决定 —— API 层（WP7）不得自行硬编码。

18 个域段
---------
==============  ===========  ================================================
域              号段         说明
==============  ===========  ================================================
``CORE``        0001-0099    内核、配置、序列化
``AUTH``        0100-0199    认证与鉴权
``DB``          0200-0299    持久化
``RUN``         0300-0399    Run 生命周期
``TASK``        0400-0499    Task 生命周期
``LEASE``       0500-0599    租约与 fence
``STEP``        0600-0699    Step 幂等
``PROVIDER``    0700-0799    模型接入
``EXECUTOR``    0800-0899    执行沙箱
``TOOL``        0900-0999    工具调用
``POLICY``      1000-1099    策略与审批
``BUDGET``      1100-1199    预算
``MEMORY``      1200-1299    上下文治理
``VCS``         1300-1399    版本控制 / GitHub
``VERIFY``      1400-1499    验证闭环
``API``         1500-1599    HTTP 接口层
``ROLE``        1600-1699    智能体角色
``EVOLUTION``   1700-1799    自演化
==============  ===========  ================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "OK_CODE",
    "ErrorCode",
    "ErrorDomain",
    "ErrorSpec",
    "ERROR_REGISTRY",
    "DOMAIN_RANGES",
    "code_to_http",
    "code_to_message",
    "is_retryable",
    "spec_of",
    "domain_of",
    "all_codes",
]

#: 统一响应格式中的「成功」码（`ARCHITECTURE.md` §10.1）。
OK_CODE = "OK"


class ErrorDomain(StrEnum):
    """错误码域段（18 个，已冻结）。"""

    CORE = "CORE"
    AUTH = "AUTH"
    DB = "DB"
    RUN = "RUN"
    TASK = "TASK"
    LEASE = "LEASE"
    STEP = "STEP"
    PROVIDER = "PROVIDER"
    EXECUTOR = "EXECUTOR"
    TOOL = "TOOL"
    POLICY = "POLICY"
    BUDGET = "BUDGET"
    MEMORY = "MEMORY"
    VCS = "VCS"
    VERIFY = "VERIFY"
    API = "API"
    ROLE = "ROLE"
    EVOLUTION = "EVOLUTION"


#: 域 → 号段（闭区间）。新增码必须落在本域号段内。
DOMAIN_RANGES: dict[ErrorDomain, tuple[int, int]] = {
    ErrorDomain.CORE: (1, 99),
    ErrorDomain.AUTH: (100, 199),
    ErrorDomain.DB: (200, 299),
    ErrorDomain.RUN: (300, 399),
    ErrorDomain.TASK: (400, 499),
    ErrorDomain.LEASE: (500, 599),
    ErrorDomain.STEP: (600, 699),
    ErrorDomain.PROVIDER: (700, 799),
    ErrorDomain.EXECUTOR: (800, 899),
    ErrorDomain.TOOL: (900, 999),
    ErrorDomain.POLICY: (1000, 1099),
    ErrorDomain.BUDGET: (1100, 1199),
    ErrorDomain.MEMORY: (1200, 1299),
    ErrorDomain.VCS: (1300, 1399),
    ErrorDomain.VERIFY: (1400, 1499),
    ErrorDomain.API: (1500, 1599),
    ErrorDomain.ROLE: (1600, 1699),
    ErrorDomain.EVOLUTION: (1700, 1799),
}


class ErrorCode(StrEnum):
    """全量错误码枚举。成员值即对外暴露的字符串码。"""

    # ---------------- CORE 0001-0099 ----------------
    CORE_INVALID_CONFIG = "LF-CORE-0001"
    CORE_INTERNAL = "LF-CORE-0002"
    CORE_SERIALIZATION_FAILED = "LF-CORE-0003"
    CORE_INVALID_ARGUMENT = "LF-CORE-0004"
    CORE_FEATURE_DISABLED = "LF-CORE-0005"
    CORE_TIMEOUT = "LF-CORE-0006"
    CORE_CONFLICT = "LF-CORE-0007"
    CORE_DEPENDENCY_UNAVAILABLE = "LF-CORE-0008"
    CORE_CIRCUIT_OPEN = "LF-CORE-0009"
    CORE_RATE_LIMITED = "LF-CORE-0010"

    # ---------------- AUTH 0100-0199 ----------------
    AUTH_UNAUTHENTICATED = "LF-AUTH-0101"
    AUTH_FORBIDDEN = "LF-AUTH-0102"
    AUTH_API_KEY_EXPIRED = "LF-AUTH-0103"
    AUTH_API_KEY_REVOKED = "LF-AUTH-0104"
    AUTH_API_KEY_INVALID = "LF-AUTH-0105"
    AUTH_USER_NOT_FOUND = "LF-AUTH-0106"
    AUTH_BAD_CREDENTIALS = "LF-AUTH-0107"
    AUTH_USER_DISABLED = "LF-AUTH-0108"

    # ---------------- DB 0200-0299 ----------------
    DB_CONNECTION_FAILED = "LF-DB-0201"
    DB_MIGRATION_PENDING = "LF-DB-0202"
    DB_UNIQUE_VIOLATION = "LF-DB-0203"
    DB_FOREIGN_KEY_VIOLATION = "LF-DB-0204"
    DB_DEADLOCK = "LF-DB-0205"
    DB_NOT_FOUND = "LF-DB-0206"
    DB_OPTIMISTIC_LOCK = "LF-DB-0207"
    DB_QUERY_TIMEOUT = "LF-DB-0208"

    # ---------------- RUN 0300-0399 ----------------
    RUN_NOT_FOUND = "LF-RUN-0301"
    RUN_ILLEGAL_TRANSITION = "LF-RUN-0302"
    RUN_MAX_LOOPS_EXCEEDED = "LF-RUN-0303"
    RUN_ALREADY_TERMINAL = "LF-RUN-0304"
    RUN_MAX_RECOVERIES_EXCEEDED = "LF-RUN-0305"
    RUN_INVALID_CONFIG_SNAPSHOT = "LF-RUN-0306"

    # ---------------- TASK 0400-0499 ----------------
    TASK_NOT_FOUND = "LF-TASK-0401"
    TASK_DEPENDENCY_CYCLE = "LF-TASK-0402"
    TASK_ILLEGAL_TRANSITION = "LF-TASK-0403"
    TASK_MAX_ATTEMPTS_EXCEEDED = "LF-TASK-0404"
    TASK_PATH_CONFLICT = "LF-TASK-0405"
    TASK_VERSION_CONFLICT = "LF-TASK-0406"
    TASK_DUPLICATE_KEY = "LF-TASK-0407"

    # ---------------- LEASE 0500-0599 ----------------
    LEASE_ACQUIRE_FAILED = "LF-LEASE-0501"
    LEASE_LOST = "LF-LEASE-0502"
    LEASE_FENCED_OUT = "LF-LEASE-0503"
    LEASE_NOT_FOUND = "LF-LEASE-0504"
    LEASE_EXPIRED = "LF-LEASE-0505"
    LEASE_MAX_EXTENDS_EXCEEDED = "LF-LEASE-0506"
    LEASE_TTL_TOO_LARGE = "LF-LEASE-0507"
    LEASE_WORKER_MISMATCH = "LF-LEASE-0508"
    LEASE_MAX_RECLAIMS_EXCEEDED = "LF-LEASE-0509"

    # ---------------- STEP 0600-0699 ----------------
    STEP_IDEMPOTENCY_CONFLICT = "LF-STEP-0601"
    STEP_UNCERTAIN = "LF-STEP-0602"
    STEP_PROBE_FAILED = "LF-STEP-0603"
    STEP_NOT_FOUND = "LF-STEP-0604"
    STEP_ILLEGAL_TRANSITION = "LF-STEP-0605"

    # ---------------- PROVIDER 0700-0799 ----------------
    PROVIDER_AUTH_FAILED = "LF-PROVIDER-0701"
    PROVIDER_RATE_LIMITED = "LF-PROVIDER-0702"
    PROVIDER_TIMEOUT = "LF-PROVIDER-0703"
    PROVIDER_SERVER_ERROR = "LF-PROVIDER-0704"
    PROVIDER_ALL_UNAVAILABLE = "LF-PROVIDER-0705"
    PROVIDER_MODEL_UNSUPPORTED = "LF-PROVIDER-0706"
    PROVIDER_NOT_REGISTERED = "LF-PROVIDER-0707"
    PROVIDER_BAD_RESPONSE = "LF-PROVIDER-0708"
    PROVIDER_CONTENT_FILTERED = "LF-PROVIDER-0709"

    # ---------------- EXECUTOR 0800-0899 ----------------
    EXECUTOR_PATH_ESCAPE = "LF-EXECUTOR-0801"
    EXECUTOR_TIMEOUT = "LF-EXECUTOR-0802"
    EXECUTOR_OOM = "LF-EXECUTOR-0803"
    EXECUTOR_CONTAINER_UNAVAILABLE = "LF-EXECUTOR-0804"
    EXECUTOR_OUTPUT_LIMIT = "LF-EXECUTOR-0805"
    EXECUTOR_WORKSPACE_NOT_READY = "LF-EXECUTOR-0806"
    EXECUTOR_NONZERO_EXIT = "LF-EXECUTOR-0807"

    # ---------------- TOOL 0900-0999 ----------------
    TOOL_ARGS_INVALID = "LF-TOOL-0901"
    TOOL_EXECUTION_FAILED = "LF-TOOL-0902"
    TOOL_NOT_REGISTERED = "LF-TOOL-0903"
    TOOL_ROLE_FORBIDDEN = "LF-TOOL-0904"
    TOOL_CONFIRMATION_REQUIRED = "LF-TOOL-0905"

    # ---------------- POLICY 1000-1099 ----------------
    POLICY_DENIED = "LF-POLICY-1001"
    POLICY_APPROVAL_REQUIRED = "LF-POLICY-1002"
    POLICY_PARSE_FAILED = "LF-POLICY-1003"
    POLICY_NOT_FOUND = "LF-POLICY-1004"
    POLICY_APPROVAL_TIMEOUT = "LF-POLICY-1005"
    POLICY_APPROVAL_ALREADY_DECIDED = "LF-POLICY-1006"

    # ---------------- BUDGET 1100-1199 ----------------
    BUDGET_EXHAUSTED = "LF-BUDGET-1101"
    BUDGET_THROTTLED = "LF-BUDGET-1102"
    BUDGET_INVALID_CONFIG = "LF-BUDGET-1103"
    BUDGET_NOT_FOUND = "LF-BUDGET-1104"

    # ---------------- MEMORY 1200-1299 ----------------
    MEMORY_CONTEXT_OVERFLOW = "LF-MEMORY-1201"
    MEMORY_PINNED_QUOTA_EXCEEDED = "LF-MEMORY-1202"
    MEMORY_CONSTRAINT_VIOLATED = "LF-MEMORY-1203"
    MEMORY_RETRIEVAL_FAILED = "LF-MEMORY-1204"
    MEMORY_INDEX_NOT_BUILT = "LF-MEMORY-1205"

    # ---------------- VCS 1300-1399 ----------------
    VCS_CLONE_FAILED = "LF-VCS-1301"
    VCS_CONFLICT = "LF-VCS-1302"
    VCS_PROTECTED_BRANCH = "LF-VCS-1303"
    VCS_PR_CREATE_FAILED = "LF-VCS-1304"
    VCS_WEBHOOK_SIGNATURE_INVALID = "LF-VCS-1305"
    VCS_REPO_NOT_FOUND = "LF-VCS-1306"
    VCS_PUSH_REJECTED = "LF-VCS-1307"

    # ---------------- VERIFY 1400-1499 ----------------
    VERIFY_BUILD_FAILED = "LF-VERIFY-1401"
    VERIFY_UNIT_FAILED = "LF-VERIFY-1402"
    VERIFY_E2E_FAILED = "LF-VERIFY-1403"
    VERIFY_LIFECYCLE_FAILED = "LF-VERIFY-1404"
    VERIFY_TIMEOUT = "LF-VERIFY-1405"

    # ---------------- API 1500-1599 ----------------
    API_VALIDATION_FAILED = "LF-API-1501"
    API_RATE_LIMITED = "LF-API-1502"
    API_IDEMPOTENCY_CONFLICT = "LF-API-1503"
    API_NOT_FOUND = "LF-API-1504"
    API_PAYLOAD_TOO_LARGE = "LF-API-1505"
    API_UNSUPPORTED_MEDIA_TYPE = "LF-API-1506"
    API_INVALID_CURSOR = "LF-API-1507"

    # ---------------- ROLE 1600-1699 ----------------
    ROLE_OUTPUT_PARSE_FAILED = "LF-ROLE-1601"
    ROLE_TOOL_FORBIDDEN = "LF-ROLE-1602"
    ROLE_PROMPT_MISSING = "LF-ROLE-1603"
    ROLE_UNAVAILABLE = "LF-ROLE-1604"

    # ---------------- EVOLUTION 1700-1799 ----------------
    EVOLUTION_SOURCE_FETCH_FAILED = "LF-EVOLUTION-1701"
    EVOLUTION_NORMALIZE_FAILED = "LF-EVOLUTION-1702"
    EVOLUTION_ADMISSION_REJECTED = "LF-EVOLUTION-1703"


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """错误码四元组。"""

    code: ErrorCode
    http_status: int
    message_zh: str
    retryable: bool

    @property
    def domain(self) -> ErrorDomain:
        """从 ``LF-<DOMAIN>-<NNNN>`` 中解析域段。"""
        return ErrorDomain(self.code.value.split("-")[1])

    @property
    def number(self) -> int:
        """从 ``LF-<DOMAIN>-<NNNN>`` 中解析数字部分。"""
        return int(self.code.value.split("-")[2])


def _spec(code: ErrorCode, http_status: int, message_zh: str, retryable: bool = False) -> ErrorSpec:
    return ErrorSpec(code=code, http_status=http_status, message_zh=message_zh, retryable=retryable)


#: 全量错误码注册表：错误码 → 四元组。
ERROR_REGISTRY: dict[ErrorCode, ErrorSpec] = {
    spec.code: spec
    for spec in (
        # CORE
        _spec(ErrorCode.CORE_INVALID_CONFIG, 500, "配置无效"),
        _spec(ErrorCode.CORE_INTERNAL, 500, "内部错误"),
        _spec(ErrorCode.CORE_SERIALIZATION_FAILED, 500, "序列化失败"),
        _spec(ErrorCode.CORE_INVALID_ARGUMENT, 400, "参数非法"),
        _spec(ErrorCode.CORE_FEATURE_DISABLED, 501, "功能未启用"),
        _spec(ErrorCode.CORE_TIMEOUT, 504, "操作超时", True),
        _spec(ErrorCode.CORE_CONFLICT, 409, "资源状态冲突"),
        _spec(ErrorCode.CORE_DEPENDENCY_UNAVAILABLE, 503, "依赖服务不可用", True),
        _spec(ErrorCode.CORE_CIRCUIT_OPEN, 503, "熔断器已打开", True),
        _spec(ErrorCode.CORE_RATE_LIMITED, 429, "本地限流触发", True),
        # AUTH
        _spec(ErrorCode.AUTH_UNAUTHENTICATED, 401, "未认证"),
        _spec(ErrorCode.AUTH_FORBIDDEN, 403, "无权限"),
        _spec(ErrorCode.AUTH_API_KEY_EXPIRED, 401, "API Key 已过期"),
        _spec(ErrorCode.AUTH_API_KEY_REVOKED, 401, "API Key 已吊销"),
        _spec(ErrorCode.AUTH_API_KEY_INVALID, 401, "API Key 无效"),
        _spec(ErrorCode.AUTH_USER_NOT_FOUND, 404, "用户不存在"),
        _spec(ErrorCode.AUTH_BAD_CREDENTIALS, 401, "用户名或密码错误"),
        _spec(ErrorCode.AUTH_USER_DISABLED, 403, "用户已被禁用"),
        # DB
        _spec(ErrorCode.DB_CONNECTION_FAILED, 503, "数据库连接失败", True),
        _spec(ErrorCode.DB_MIGRATION_PENDING, 500, "数据库迁移未应用"),
        _spec(ErrorCode.DB_UNIQUE_VIOLATION, 409, "唯一约束冲突"),
        _spec(ErrorCode.DB_FOREIGN_KEY_VIOLATION, 409, "外键约束冲突"),
        _spec(ErrorCode.DB_DEADLOCK, 409, "事务死锁", True),
        _spec(ErrorCode.DB_NOT_FOUND, 404, "记录不存在"),
        _spec(ErrorCode.DB_OPTIMISTIC_LOCK, 409, "乐观锁版本冲突", True),
        _spec(ErrorCode.DB_QUERY_TIMEOUT, 504, "数据库查询超时", True),
        # RUN
        _spec(ErrorCode.RUN_NOT_FOUND, 404, "Run 不存在"),
        _spec(ErrorCode.RUN_ILLEGAL_TRANSITION, 409, "Run 非法状态迁移"),
        _spec(ErrorCode.RUN_MAX_LOOPS_EXCEEDED, 409, "Run 超过最大轮次"),
        _spec(ErrorCode.RUN_ALREADY_TERMINAL, 409, "Run 已处于终态"),
        _spec(ErrorCode.RUN_MAX_RECOVERIES_EXCEEDED, 409, "Run 超过最大恢复次数"),
        _spec(ErrorCode.RUN_INVALID_CONFIG_SNAPSHOT, 400, "Run 配置快照无效"),
        # TASK
        _spec(ErrorCode.TASK_NOT_FOUND, 404, "Task 不存在"),
        _spec(ErrorCode.TASK_DEPENDENCY_CYCLE, 400, "Task 依赖存在环"),
        _spec(ErrorCode.TASK_ILLEGAL_TRANSITION, 409, "Task 非法状态迁移"),
        _spec(ErrorCode.TASK_MAX_ATTEMPTS_EXCEEDED, 409, "Task 超过最大重试次数"),
        _spec(ErrorCode.TASK_PATH_CONFLICT, 409, "Task 触达路径与在途任务冲突"),
        _spec(ErrorCode.TASK_VERSION_CONFLICT, 409, "Task 版本冲突（CAS 失败）", True),
        _spec(ErrorCode.TASK_DUPLICATE_KEY, 409, "同一 Run 内 task_key 重复"),
        # LEASE
        _spec(ErrorCode.LEASE_ACQUIRE_FAILED, 409, "租约领取失败（已被其他 Worker 抢占）", True),
        _spec(ErrorCode.LEASE_LOST, 409, "租约已丢失"),
        _spec(ErrorCode.LEASE_FENCED_OUT, 409, "已被 Fence（fence_token 过期）"),
        _spec(ErrorCode.LEASE_NOT_FOUND, 404, "租约不存在"),
        _spec(ErrorCode.LEASE_EXPIRED, 409, "租约已过期"),
        _spec(ErrorCode.LEASE_MAX_EXTENDS_EXCEEDED, 409, "租约续期次数超限"),
        _spec(ErrorCode.LEASE_TTL_TOO_LARGE, 400, "租约 TTL 超过上限"),
        _spec(ErrorCode.LEASE_WORKER_MISMATCH, 403, "租约不属于该 Worker"),
        _spec(ErrorCode.LEASE_MAX_RECLAIMS_EXCEEDED, 409, "租约回收次数超限"),
        # STEP
        _spec(ErrorCode.STEP_IDEMPOTENCY_CONFLICT, 409, "幂等键冲突"),
        _spec(ErrorCode.STEP_UNCERTAIN, 409, "Step 副作用状态不确定"),
        _spec(ErrorCode.STEP_PROBE_FAILED, 502, "Step 副作用探测失败", True),
        _spec(ErrorCode.STEP_NOT_FOUND, 404, "Step 不存在"),
        _spec(ErrorCode.STEP_ILLEGAL_TRANSITION, 409, "Step 非法状态迁移"),
        # PROVIDER
        _spec(ErrorCode.PROVIDER_AUTH_FAILED, 401, "Provider 认证失败"),
        _spec(ErrorCode.PROVIDER_RATE_LIMITED, 429, "Provider 限流", True),
        _spec(ErrorCode.PROVIDER_TIMEOUT, 504, "Provider 请求超时", True),
        _spec(ErrorCode.PROVIDER_SERVER_ERROR, 502, "Provider 服务端错误", True),
        _spec(ErrorCode.PROVIDER_ALL_UNAVAILABLE, 503, "全部 Provider 不可用", True),
        _spec(ErrorCode.PROVIDER_MODEL_UNSUPPORTED, 400, "Provider 不支持该模型"),
        _spec(ErrorCode.PROVIDER_NOT_REGISTERED, 404, "Provider 未注册"),
        _spec(ErrorCode.PROVIDER_BAD_RESPONSE, 502, "Provider 响应解析失败", True),
        _spec(ErrorCode.PROVIDER_CONTENT_FILTERED, 400, "内容被 Provider 安全策略拦截"),
        # EXECUTOR
        _spec(ErrorCode.EXECUTOR_PATH_ESCAPE, 403, "路径越界（逃逸出工作区）"),
        _spec(ErrorCode.EXECUTOR_TIMEOUT, 504, "命令执行超时", True),
        _spec(ErrorCode.EXECUTOR_OOM, 507, "命令执行内存超限"),
        _spec(ErrorCode.EXECUTOR_CONTAINER_UNAVAILABLE, 503, "容器运行时不可用", True),
        _spec(ErrorCode.EXECUTOR_OUTPUT_LIMIT, 413, "命令输出超过上限"),
        _spec(ErrorCode.EXECUTOR_WORKSPACE_NOT_READY, 409, "工作区尚未准备好"),
        _spec(ErrorCode.EXECUTOR_NONZERO_EXIT, 400, "命令以非零状态退出"),
        # TOOL
        _spec(ErrorCode.TOOL_ARGS_INVALID, 400, "工具参数校验失败"),
        _spec(ErrorCode.TOOL_EXECUTION_FAILED, 500, "工具执行失败"),
        _spec(ErrorCode.TOOL_NOT_REGISTERED, 404, "工具未注册"),
        _spec(ErrorCode.TOOL_ROLE_FORBIDDEN, 403, "当前角色无权调用该工具"),
        _spec(ErrorCode.TOOL_CONFIRMATION_REQUIRED, 409, "该工具需要显式确认"),
        # POLICY
        _spec(ErrorCode.POLICY_DENIED, 403, "策略拒绝执行"),
        _spec(ErrorCode.POLICY_APPROVAL_REQUIRED, 409, "该动作需要人工审批"),
        _spec(ErrorCode.POLICY_PARSE_FAILED, 400, "策略解析失败"),
        _spec(ErrorCode.POLICY_NOT_FOUND, 404, "策略不存在"),
        _spec(ErrorCode.POLICY_APPROVAL_TIMEOUT, 408, "审批等待超时"),
        _spec(ErrorCode.POLICY_APPROVAL_ALREADY_DECIDED, 409, "审批已被裁决"),
        # BUDGET
        _spec(ErrorCode.BUDGET_EXHAUSTED, 402, "预算已耗尽"),
        _spec(ErrorCode.BUDGET_THROTTLED, 429, "预算限流中", True),
        _spec(ErrorCode.BUDGET_INVALID_CONFIG, 400, "预算配置无效"),
        _spec(ErrorCode.BUDGET_NOT_FOUND, 404, "预算不存在"),
        # MEMORY
        _spec(ErrorCode.MEMORY_CONTEXT_OVERFLOW, 413, "上下文超限且不可再压缩"),
        _spec(ErrorCode.MEMORY_PINNED_QUOTA_EXCEEDED, 409, "Pinned 约束配额超限"),
        _spec(ErrorCode.MEMORY_CONSTRAINT_VIOLATED, 409, "钉死约束被违背"),
        _spec(ErrorCode.MEMORY_RETRIEVAL_FAILED, 500, "记忆检索失败"),
        _spec(ErrorCode.MEMORY_INDEX_NOT_BUILT, 409, "检索索引尚未构建"),
        # VCS
        _spec(ErrorCode.VCS_CLONE_FAILED, 502, "仓库克隆失败", True),
        _spec(ErrorCode.VCS_CONFLICT, 409, "代码合并冲突"),
        _spec(ErrorCode.VCS_PROTECTED_BRANCH, 403, "目标分支受保护"),
        _spec(ErrorCode.VCS_PR_CREATE_FAILED, 502, "PR 创建失败", True),
        _spec(ErrorCode.VCS_WEBHOOK_SIGNATURE_INVALID, 401, "Webhook 签名无效"),
        _spec(ErrorCode.VCS_REPO_NOT_FOUND, 404, "仓库不存在"),
        _spec(ErrorCode.VCS_PUSH_REJECTED, 409, "推送被远端拒绝"),
        # VERIFY
        _spec(ErrorCode.VERIFY_BUILD_FAILED, 422, "构建校验失败"),
        _spec(ErrorCode.VERIFY_UNIT_FAILED, 422, "单元测试校验失败"),
        _spec(ErrorCode.VERIFY_E2E_FAILED, 422, "端到端校验失败"),
        _spec(ErrorCode.VERIFY_LIFECYCLE_FAILED, 422, "生命周期校验失败"),
        _spec(ErrorCode.VERIFY_TIMEOUT, 504, "校验超时", True),
        # API
        _spec(ErrorCode.API_VALIDATION_FAILED, 422, "请求校验失败"),
        _spec(ErrorCode.API_RATE_LIMITED, 429, "请求过于频繁", True),
        _spec(ErrorCode.API_IDEMPOTENCY_CONFLICT, 409, "幂等键冲突"),
        _spec(ErrorCode.API_NOT_FOUND, 404, "资源不存在"),
        _spec(ErrorCode.API_PAYLOAD_TOO_LARGE, 413, "请求体过大"),
        _spec(ErrorCode.API_UNSUPPORTED_MEDIA_TYPE, 415, "不支持的媒体类型"),
        _spec(ErrorCode.API_INVALID_CURSOR, 400, "分页游标无效"),
        # ROLE
        _spec(ErrorCode.ROLE_OUTPUT_PARSE_FAILED, 422, "角色输出解析失败"),
        _spec(ErrorCode.ROLE_TOOL_FORBIDDEN, 403, "角色越权调用工具"),
        _spec(ErrorCode.ROLE_PROMPT_MISSING, 500, "Prompt 模板缺失"),
        _spec(ErrorCode.ROLE_UNAVAILABLE, 503, "角色当前不可用"),
        # EVOLUTION
        _spec(ErrorCode.EVOLUTION_SOURCE_FETCH_FAILED, 502, "演化来源采集失败", True),
        _spec(ErrorCode.EVOLUTION_NORMALIZE_FAILED, 422, "演化条目归一化失败"),
        _spec(ErrorCode.EVOLUTION_ADMISSION_REJECTED, 403, "演化条目准入被拒"),
    )
}


def _coerce(code: ErrorCode | str) -> ErrorCode | None:
    """把字符串安全地转换为 :class:`ErrorCode`；未知码返回 ``None``。"""
    if isinstance(code, ErrorCode):
        return code
    try:
        return ErrorCode(code)
    except ValueError:
        return None


def spec_of(code: ErrorCode | str) -> ErrorSpec:
    """返回错误码的四元组；未知码降级为 ``LF-CORE-0002``。"""
    resolved = _coerce(code)
    if resolved is None:
        return ERROR_REGISTRY[ErrorCode.CORE_INTERNAL]
    return ERROR_REGISTRY[resolved]


def code_to_http(code: ErrorCode | str) -> int:
    """错误码 → HTTP 状态码（**API 层唯一映射入口**）。

    :param code: 错误码枚举或字符串；``"OK"`` 返回 200。
    """
    if code == OK_CODE:
        return 200
    return spec_of(code).http_status


def code_to_message(code: ErrorCode | str) -> str:
    """错误码 → 默认中文消息。"""
    if code == OK_CODE:
        return ""
    return spec_of(code).message_zh


def is_retryable(code: ErrorCode | str) -> bool:
    """该错误码是否可以安全重试。"""
    if code == OK_CODE:
        return False
    return spec_of(code).retryable


def domain_of(code: ErrorCode | str) -> ErrorDomain:
    """解析错误码所属域段。"""
    return spec_of(code).domain


def all_codes() -> tuple[ErrorCode, ...]:
    """按注册顺序返回全部错误码。"""
    return tuple(ERROR_REGISTRY.keys())
