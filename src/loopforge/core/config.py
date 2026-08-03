"""配置树与七层覆盖加载器。

七层覆盖顺序（``ARCHITECTURE.md`` §2.3，**后覆盖前**）::

    1. 代码内默认值（本模块各字段 default）
    2. config/default.yaml
    3. $LOOPFORGE_CONFIG_FILE 指定的 YAML（默认 ./loopforge.yaml，存在才加载）
    4. 环境变量（前缀 LOOPFORGE__，双下划线表示嵌套）
    5. .env 文件
    6. 数据库中的热配置（provider_configs / policies）
    7. CLI 显式参数 / API 请求体字段

.. note::
   第 5 层排在第 4 层之后是文档明确规定的（``.env`` 覆盖真实环境变量），
   与部分框架的惯例相反，实现时严格照文档执行。

使用方式::

    from loopforge.core.config import get_settings
    settings = get_settings()
    print(settings.db.url)
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from loopforge.core.errors import ConfigError

__all__ = [
    "AppSettings",
    "ApiSettings",
    "DbSettings",
    "DispatcherSettings",
    "ExecutorSettings",
    "GovernanceSettings",
    "LeaseSettings",
    "MemorySettings",
    "ObservabilitySettings",
    "OrchestrationSettings",
    "ProviderSettings",
    "RecoverySettings",
    "RolesSettings",
    "SecuritySettings",
    "Settings",
    "VcsSettings",
    "VerificationSettings",
    "WorkerSettings",
    "CONFIG_LAYERS",
    "ENV_PREFIX",
    "ENV_NESTED_DELIMITER",
    "get_settings",
    "reload_settings",
    "set_settings",
    "load_settings",
    "project_root",
    "default_config_path",
]

#: 环境变量前缀。
ENV_PREFIX: Final[str] = "LOOPFORGE__"

#: 环境变量嵌套分隔符。
ENV_NESTED_DELIMITER: Final[str] = "__"

#: 七层名称（供 ``test_config.py`` 断言顺序完整性）。
CONFIG_LAYERS: Final[tuple[str, ...]] = (
    "code_default",
    "default_yaml",
    "config_file_yaml",
    "environ",
    "dotenv",
    "database",
    "cli",
)


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------


class _Base(BaseModel):
    """所有配置子树的基类：禁止未知键，赋值时重新校验。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, frozen=False)


# ---------------------------------------------------------------------------
# 子树定义（键名与 config/default.yaml 一一对应）
# ---------------------------------------------------------------------------


class AppSettings(_Base):
    """应用级配置。"""

    name: str = "loopforge"
    env: Literal["development", "test", "production"] = "development"
    timezone: str = "UTC"
    version: str = "0.1.0"
    data_dir: Path = Path("./data")


class DbSettings(_Base):
    """数据库配置。"""

    url: str = "sqlite+aiosqlite:///./data/loopforge.db"
    pool_size: int = Field(default=10, ge=1, le=1000)
    max_overflow: int = Field(default=20, ge=0, le=1000)
    pool_recycle_s: int = Field(default=1800, ge=-1)
    pool_pre_ping: bool = True
    echo: bool = False
    statement_timeout_s: int = Field(default=30, ge=1)
    sqlite_busy_timeout_ms: int = Field(default=5000, ge=0)
    sqlite_journal_mode: Literal["WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY", "OFF"] = "WAL"

    @property
    def is_sqlite(self) -> bool:
        """当前是否为 SQLite 方言。"""
        return self.url.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        """当前是否为 PostgreSQL 方言。"""
        return self.url.startswith("postgresql")


class WsSettings(_Base):
    """WebSocket 子配置。"""

    heartbeat_interval_s: int = Field(default=20, ge=1)
    max_backfill: int = Field(default=5000, ge=0)
    send_queue_size: int = Field(default=1000, ge=1)


class ApiSettings(_Base):
    """HTTP API 配置。"""

    host: str = "0.0.0.0"  # noqa: S104 —— 自托管服务默认监听全网卡
    port: int = Field(default=8080, ge=1, le=65535)
    root_path: str = ""
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    rate_limit_per_minute: int = Field(default=600, ge=1)
    max_page_size: int = Field(default=200, ge=1)
    default_page_size: int = Field(default=50, ge=1)
    request_timeout_s: int = Field(default=60, ge=1)
    docs_enabled: bool = True
    ws: WsSettings = Field(default_factory=WsSettings)

    @field_validator("default_page_size")
    @classmethod
    def _check_page_size(cls, value: int, info: Any) -> int:
        """默认分页不得超过上限。"""
        max_size = info.data.get("max_page_size")
        if isinstance(max_size, int) and value > max_size:
            raise ValueError(f"default_page_size({value}) 不能大于 max_page_size({max_size})")
        return value


class SecuritySettings(_Base):
    """安全与密钥配置。"""

    encryption_key_env: str = "LOOPFORGE__SECURITY__ENCRYPTION_KEY"
    encryption_key: str = ""
    api_key_prefix: str = "lf"
    default_admin_key_env: str = "LOOPFORGE__SECURITY__BOOTSTRAP_KEY"
    bootstrap_key: str = ""
    api_key_ttl_days: int = Field(default=365, ge=1)
    password_min_length: int = Field(default=12, ge=8)


class CircuitBreakerSettings(_Base):
    """熔断器配置。"""

    failure_threshold: int = Field(default=5, ge=1)
    recovery_timeout_s: int = Field(default=60, ge=1)
    half_open_max_calls: int = Field(default=2, ge=1)


class ProviderSettings(_Base):
    """Provider 层配置（WP2 只改值不加节）。"""

    default: str = "mock"
    failover_chain: list[str] = Field(default_factory=lambda: ["mock"])
    request_timeout_s: int = Field(default=120, ge=1)
    max_retries: int = Field(default=4, ge=0)
    backoff_base_s: float = Field(default=1.0, gt=0)
    backoff_max_s: float = Field(default=30.0, gt=0)
    circuit_breaker: CircuitBreakerSettings = Field(default_factory=CircuitBreakerSettings)
    pricing_file: Path = Path("config/pricing.yaml")
    stream_enabled: bool = True


class RoleBinding(_Base):
    """单个角色的模型绑定。"""

    provider: str = "mock"
    model: str = "mock-large"
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)


class RolesSettings(_Base):
    """五个智能体角色的模型绑定。"""

    planner: RoleBinding = Field(
        default_factory=lambda: RoleBinding(temperature=0.2, max_tokens=4096)
    )
    coder: RoleBinding = Field(
        default_factory=lambda: RoleBinding(temperature=0.1, max_tokens=8192)
    )
    reviewer: RoleBinding = Field(
        default_factory=lambda: RoleBinding(temperature=0.0, max_tokens=4096)
    )
    tester: RoleBinding = Field(
        default_factory=lambda: RoleBinding(temperature=0.1, max_tokens=4096)
    )
    summarizer: RoleBinding = Field(
        default_factory=lambda: RoleBinding(
            model="mock-small", temperature=0.0, max_tokens=2048
        )
    )

    def for_role(self, role: str) -> RoleBinding:
        """按角色名取绑定。

        Args:
            role: 角色名，大小写不敏感。

        Returns:
            对应的 :class:`RoleBinding`。

        Raises:
            ConfigError: 角色名未知。
        """
        key = role.strip().lower()
        binding = getattr(self, key, None)
        if not isinstance(binding, RoleBinding):
            raise ConfigError(f"未知角色 {role!r}", details={"role": role})
        return binding


class DockerExecutorSettings(_Base):
    """Docker 执行器子配置。"""

    image: str = "python:3.11-slim"
    network: str = "none"
    cpu_quota: float = Field(default=2.0, gt=0)
    memory_limit: str = "2g"
    reuse_container: bool = True


class ExecutorSettings(_Base):
    """执行沙箱配置（WP3）。"""

    kind: Literal["local_subprocess", "docker"] = "local_subprocess"
    workspace_root: Path = Path("./data/workspaces")
    command_timeout_s: int = Field(default=300, ge=1)
    max_output_bytes: int = Field(default=1_048_576, ge=1024)
    max_memory_mb: int = Field(default=2048, ge=64)
    env_allowlist: list[str] = Field(
        default_factory=lambda: [
            "PATH",
            "HOME",
            "LANG",
            "PYTHONPATH",
            "TMPDIR",
            "SystemRoot",
            "TEMP",
            "COMSPEC",
        ]
    )
    cleanup_on_success: bool = True
    docker: DockerExecutorSettings = Field(default_factory=DockerExecutorSettings)


class LayerRatios(_Base):
    """五层记忆的 token 配额比例。"""

    pinned: float = Field(default=0.08, ge=0.0, le=1.0)
    working: float = Field(default=0.40, ge=0.0, le=1.0)
    episodic: float = Field(default=0.22, ge=0.0, le=1.0)
    semantic: float = Field(default=0.20, ge=0.0, le=1.0)
    milestone: float = Field(default=0.10, ge=0.0, le=1.0)

    def total(self) -> float:
        """五层比例之和。"""
        return self.pinned + self.working + self.episodic + self.semantic + self.milestone


class Bm25Settings(_Base):
    """BM25 检索参数。"""

    k1: float = Field(default=1.5, gt=0)
    b: float = Field(default=0.75, ge=0.0, le=1.0)
    top_k: int = Field(default=40, ge=1)


class EmbeddingSettings(_Base):
    """向量检索参数。"""

    enabled: bool = False
    model: str = "text-embedding-3-small"
    top_k: int = Field(default=20, ge=1)
    fusion: Literal["rrf", "weighted"] = "rrf"


class RetrievalSettings(_Base):
    """检索总配置。"""

    default: Literal["bm25", "embedding", "hybrid"] = "bm25"
    bm25: Bm25Settings = Field(default_factory=Bm25Settings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)


class MemorySettings(_Base):
    """上下文治理配置（WP5）。"""

    max_context_tokens: int = Field(default=128_000, ge=1024)
    usage_ratio: float = Field(default=0.60, gt=0.0, le=1.0)
    reserve_output_tokens: int = Field(default=8192, ge=0)
    layer_ratios: LayerRatios = Field(default_factory=LayerRatios)
    compress_every_n_loops: int = Field(default=25, ge=1)
    fidelity_check_interval: int = Field(default=20, ge=1)
    pinned_max_ratio: float = Field(default=0.08, gt=0.0, le=1.0)
    pinned_max_items: int = Field(default=50, ge=1)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)

    @property
    def budget_tokens(self) -> int:
        """实际可用于装配上下文的 token 预算。"""
        return int(self.max_context_tokens * self.usage_ratio) - self.reserve_output_tokens


class OrchestrationSettings(_Base):
    """编排引擎配置（WP4）。"""

    max_loops: int = Field(default=500, ge=1)
    loop_timeout_s: int = Field(default=1800, ge=1)
    checkpoint_every_step: bool = True
    checkpoint_every_loop: bool = True
    no_progress_threshold: int = Field(default=5, ge=1)
    action_loop_window: int = Field(default=8, ge=2)
    event_bus_queue_size: int = Field(default=10_000, ge=1)
    event_bus_overflow_policy: Literal["drop_oldest", "drop_newest", "block"] = "drop_oldest"


class LeaseSettings(_Base):
    """租约配置（WP4 并发正确性的参数面）。"""

    base_ttl_s: int = Field(default=90, ge=5)
    heartbeat_interval_s: int = Field(default=20, ge=1)
    max_ttl_s: int = Field(default=1800, ge=5)
    grace_period_s: int = Field(default=30, ge=0)
    max_extends: int = Field(default=40, ge=0)
    max_reclaims: int = Field(default=5, ge=0)

    @field_validator("max_ttl_s")
    @classmethod
    def _check_ttl(cls, value: int, info: Any) -> int:
        """``max_ttl_s`` 必须 >= ``base_ttl_s``。"""
        base = info.data.get("base_ttl_s")
        if isinstance(base, int) and value < base:
            raise ValueError(f"max_ttl_s({value}) 不能小于 base_ttl_s({base})")
        return value


class DispatcherSettings(_Base):
    """任务分发配置。"""

    strategy: Literal["fifo", "priority", "dependency_aware"] = "dependency_aware"
    batch_size: int = Field(default=20, ge=1)
    max_concurrent_tasks_per_run: int = Field(default=4, ge=1)
    starvation_threshold: int = Field(default=20, ge=1)


class RecoverySettings(_Base):
    """崩溃恢复配置。"""

    on_uncertain: Literal["replan", "ask_human", "fail"] = "replan"
    max_recoveries_per_run: int = Field(default=20, ge=0)
    reconcile_probe_timeout_s: int = Field(default=30, ge=1)


class BudgetSettings(_Base):
    """预算配置。"""

    default_run_limit_usd: float = Field(default=5.0, gt=0)
    default_day_limit_usd: float = Field(default=50.0, gt=0)
    warn_at: float = Field(default=0.7, gt=0.0, le=1.0)
    throttle_at: float = Field(default=0.9, gt=0.0, le=1.0)
    halt_at: float = Field(default=1.0, gt=0.0, le=1.0)
    throttle_sleep_s: int = Field(default=30, ge=0)

    @field_validator("halt_at")
    @classmethod
    def _check_thresholds(cls, value: float, info: Any) -> float:
        """必须满足 ``warn_at <= throttle_at <= halt_at``。"""
        warn = info.data.get("warn_at")
        throttle = info.data.get("throttle_at")
        if isinstance(warn, float) and isinstance(throttle, float):
            if not warn <= throttle <= value:
                raise ValueError(
                    f"必须满足 warn_at({warn}) <= throttle_at({throttle}) <= halt_at({value})"
                )
        return value


class GovernanceSettings(_Base):
    """治理层配置（WP5）。"""

    policy_file: Path = Path("config/policies/default.yaml")
    approval_timeout_s: int = Field(default=3600, ge=1)
    approval_timeout_action: Literal["fail", "deny", "allow"] = "fail"
    audit_chain_enabled: bool = True
    budget: BudgetSettings = Field(default_factory=BudgetSettings)


class VerificationSettings(_Base):
    """验证闭环配置（WP3）。"""

    enabled_steps: list[str] = Field(
        default_factory=lambda: ["build", "unit", "e2e", "lifecycle"]
    )
    build_timeout_s: int = Field(default=600, ge=1)
    unit_timeout_s: int = Field(default=900, ge=1)
    e2e_timeout_s: int = Field(default=1200, ge=1)
    lifecycle_timeout_s: int = Field(default=300, ge=1)
    fail_fast: bool = False


class GithubSettings(_Base):
    """GitHub 子配置。"""

    api_base: str = "https://api.github.com"
    token_env: str = "LOOPFORGE__VCS__GITHUB__TOKEN"
    token: str = ""
    auto_label: str = "loopforge:auto"
    webhook_secret_env: str = "LOOPFORGE__VCS__GITHUB__WEBHOOK_SECRET"
    webhook_secret: str = ""


class VcsSettings(_Base):
    """版本控制配置（WP6）。"""

    provider: Literal["local_sandbox", "github"] = "local_sandbox"
    branch_prefix: str = "loopforge/"
    protected_branches: list[str] = Field(
        default_factory=lambda: ["main", "master", "release/*"]
    )
    commit_trailer_prefix: str = "LoopForge"
    github: GithubSettings = Field(default_factory=GithubSettings)


class WorkerSettings(_Base):
    """Worker 运行时配置（WP4）。"""

    id: str = ""
    concurrency: int = Field(default=2, ge=1)
    poll_interval_s: float = Field(default=2.0, gt=0)
    capabilities: list[str] = Field(default_factory=lambda: ["python", "git"])
    drain_timeout_s: int = Field(default=120, ge=0)
    watchdog_interval_s: int = Field(default=10, ge=1)


class ObservabilitySettings(_Base):
    """可观测配置（WP7）。"""

    log_format: Literal["json", "console"] = "json"
    log_level: Literal["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    metrics_enabled: bool = True
    metrics_port: int = Field(default=9090, ge=1, le=65535)
    alerts_file: Path = Path("config/alerts.yaml")
    channels: list[dict[str, Any]] = Field(default_factory=list)
    redaction_enabled: bool = True
    trace_header: str = "X-Trace-Id"


class Settings(_Base):
    """配置根节点 —— 17 个子树。

    八个 §12.5 冻结顶层节：``providers`` ``executor`` ``memory`` ``governance``
    ``orchestration`` ``api`` ``worker`` ``observability``；
    其余为基础设施节。
    """

    app: AppSettings = Field(default_factory=AppSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    roles: RolesSettings = Field(default_factory=RolesSettings)
    executor: ExecutorSettings = Field(default_factory=ExecutorSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    orchestration: OrchestrationSettings = Field(default_factory=OrchestrationSettings)
    lease: LeaseSettings = Field(default_factory=LeaseSettings)
    dispatcher: DispatcherSettings = Field(default_factory=DispatcherSettings)
    recovery: RecoverySettings = Field(default_factory=RecoverySettings)
    governance: GovernanceSettings = Field(default_factory=GovernanceSettings)
    verification: VerificationSettings = Field(default_factory=VerificationSettings)
    vcs: VcsSettings = Field(default_factory=VcsSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    def dump(self) -> dict[str, Any]:
        """导出为可 JSON 化的字典（Run 的 ``config_snapshot`` 用）。

        Returns:
            纯 Python 字典。
        """
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# 层加载工具
# ---------------------------------------------------------------------------


def project_root() -> Path:
    """定位仓库根目录（``src/loopforge/core/config.py`` 向上四级）。

    Returns:
        仓库根目录的绝对路径。
    """
    return Path(__file__).resolve().parents[3]


def default_config_path() -> Path:
    """``config/default.yaml`` 的绝对路径。

    Returns:
        路径对象（不保证存在）。
    """
    override = os.environ.get("LOOPFORGE_CONFIG_DIR")
    base = Path(override) if override else project_root() / "config"
    return base / "default.yaml"


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """递归合并两层字典，``override`` 优先。

    Args:
        base: 底层字典。
        override: 覆盖字典。

    Returns:
        全新的合并结果，不修改入参。
    """
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 文件为字典。

    Args:
        path: 文件路径。

    Returns:
        解析结果；文件不存在或内容为空时返回空字典。

    Raises:
        ConfigError: YAML 语法错误或顶层不是映射。
    """
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"配置文件 {path} 解析失败：{exc}", details={"path": str(path)}
        ) from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"配置文件 {path} 顶层必须是映射，实际是 {type(raw).__name__}",
            details={"path": str(path)},
        )
    return raw


def _coerce_scalar(text: str) -> Any:
    """把环境变量字符串转换成 Python 标量。

    支持 ``true/false/null/数字/JSON 数组``，其余原样返回字符串。

    Args:
        text: 环境变量原始值。

    Returns:
        转换后的值。
    """
    stripped = text.strip()
    if stripped == "":
        return ""
    try:
        parsed = yaml.safe_load(stripped)
    except yaml.YAMLError:
        return text
    if isinstance(parsed, (dict, list, bool, int, float)) or parsed is None:
        return parsed
    return text


def _nested_from_flat(flat: Mapping[str, str], prefix: str) -> dict[str, Any]:
    """把 ``LOOPFORGE__DB__URL=xxx`` 这样的扁平键还原成嵌套字典。

    Args:
        flat: 扁平键值映射（通常是 ``os.environ``）。
        prefix: 键前缀。

    Returns:
        嵌套字典；无匹配键时返回空字典。
    """
    result: dict[str, Any] = {}
    for raw_key, raw_value in flat.items():
        if not raw_key.startswith(prefix):
            continue
        path = [part.lower() for part in raw_key[len(prefix) :].split(ENV_NESTED_DELIMITER)]
        path = [part for part in path if part]
        if not path:
            continue
        cursor: MutableMapping[str, Any] = result
        for part in path[:-1]:
            existing = cursor.get(part)
            if not isinstance(existing, dict):
                existing = {}
                cursor[part] = existing
            cursor = existing
        cursor[path[-1]] = _coerce_scalar(raw_value)
    return result


def _parse_dotenv(path: Path) -> dict[str, str]:
    """极简 ``.env`` 解析器（不引入额外依赖）。

    支持 ``KEY=VALUE``、``export KEY=VALUE``、``#`` 注释、单双引号包裹。

    Args:
        path: ``.env`` 文件路径。

    Returns:
        键值字典；文件不存在返回空字典。
    """
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def load_settings(
    *,
    config_file: Path | str | None = None,
    dotenv_file: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    db_overrides: Mapping[str, Any] | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
    include_default_yaml: bool = True,
) -> Settings:
    """按七层顺序构建 :class:`Settings`。

    Args:
        config_file: 第 3 层 YAML；``None`` 时读取 ``$LOOPFORGE_CONFIG_FILE``，
            再退回 ``<repo>/loopforge.yaml``（存在才加载）。
        dotenv_file: 第 5 层 ``.env``；``None`` 时取 ``<repo>/.env``。
        environ: 第 4 层环境变量来源；``None`` 时取 :data:`os.environ`。
        db_overrides: 第 6 层数据库热配置。
        cli_overrides: 第 7 层 CLI / API 单次覆盖。
        include_default_yaml: 是否加载第 2 层 ``config/default.yaml``；
            单测隔离时可关掉。

    Returns:
        校验通过的配置对象。

    Raises:
        ConfigError: 任一层解析失败或最终校验不通过。
    """
    env_map = dict(os.environ if environ is None else environ)
    merged: dict[str, Any] = {}

    # 第 2 层：config/default.yaml
    if include_default_yaml:
        merged = _deep_merge(merged, _load_yaml(default_config_path()))

    # 第 3 层：$LOOPFORGE_CONFIG_FILE 或 ./loopforge.yaml
    if config_file is None:
        env_path = env_map.get("LOOPFORGE_CONFIG_FILE")
        candidate = Path(env_path) if env_path else project_root() / "loopforge.yaml"
    else:
        candidate = Path(config_file)
    if candidate.is_file():
        merged = _deep_merge(merged, _load_yaml(candidate))
    elif config_file is not None:
        raise ConfigError(
            f"指定的配置文件不存在：{candidate}", details={"path": str(candidate)}
        )

    # 第 4 层：环境变量
    merged = _deep_merge(merged, _nested_from_flat(env_map, ENV_PREFIX))

    # 第 5 层：.env 文件（按文档，优先级高于真实环境变量）
    dotenv_path = Path(dotenv_file) if dotenv_file is not None else project_root() / ".env"
    merged = _deep_merge(merged, _nested_from_flat(_parse_dotenv(dotenv_path), ENV_PREFIX))

    # 第 6 层：数据库热配置
    if db_overrides:
        merged = _deep_merge(merged, db_overrides)

    # 第 7 层：CLI / API 单次覆盖
    if cli_overrides:
        merged = _deep_merge(merged, cli_overrides)

    try:
        return Settings.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(
            f"配置校验失败：{exc.error_count()} 处错误",
            details={"errors": exc.errors(include_url=False)},
        ) from exc


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------

_settings: Settings | None = None
_settings_lock = threading.RLock()


def get_settings() -> Settings:
    """取得全局配置单例（首次调用时加载）。

    Returns:
        :class:`Settings` 单例。

    Raises:
        ConfigError: 首次加载失败。
    """
    global _settings  # noqa: PLW0603 —— 单例模式必须
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                _settings = load_settings()
    return _settings


def reload_settings(**kwargs: Any) -> Settings:
    """重新加载配置并替换单例。

    Args:
        **kwargs: 透传给 :func:`load_settings`。

    Returns:
        新的配置单例。
    """
    global _settings  # noqa: PLW0603 —— 单例模式必须
    with _settings_lock:
        _settings = load_settings(**kwargs)
    return _settings


def set_settings(settings: Settings | None) -> None:
    """直接注入或清空单例（**仅测试使用**）。

    Args:
        settings: 新的配置对象；传 ``None`` 表示清空，下次 :func:`get_settings`
            会重新加载。
    """
    global _settings  # noqa: PLW0603 —— 单例模式必须
    with _settings_lock:
        _settings = settings
