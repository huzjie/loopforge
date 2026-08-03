"""``loopforge.core.config`` 单测 —— 七层覆盖顺序是本模块的生命线。

覆盖点
------
* 七层常量完整且顺序正确；
* 每一层单独生效；
* 相邻层之间「后覆盖前」；
* 环境变量的嵌套解析与标量类型推断；
* ``.env`` 优先级高于真实环境变量（文档明确规定，与多数框架相反）；
* 未知键 / 非法值一律抛 :class:`~loopforge.core.errors.ConfigError`；
* 单例的 get / set / reload 语义；
* 子树的领域方法与交叉字段校验。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loopforge.core.config import (
    CONFIG_LAYERS,
    ENV_NESTED_DELIMITER,
    ENV_PREFIX,
    BudgetSettings,
    LeaseSettings,
    MemorySettings,
    RolesSettings,
    Settings,
    default_config_path,
    get_settings,
    load_settings,
    project_root,
    reload_settings,
    set_settings,
)
from loopforge.core.errors import ConfigError

pytestmark = pytest.mark.usefixtures("clean_env")

_NO_DOTENV = Path("__no_such_dotenv__")


def _load(**kwargs: object) -> Settings:
    """加载一份「不受磁盘 .env 影响」的配置。"""
    kwargs.setdefault("include_default_yaml", False)
    kwargs.setdefault("environ", {})
    kwargs.setdefault("dotenv_file", _NO_DOTENV)
    return load_settings(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 层定义
# ---------------------------------------------------------------------------


def test_config_layers_are_frozen_and_ordered() -> None:
    """七层名称与顺序必须与 ARCHITECTURE.md §2.3 完全一致。"""
    assert CONFIG_LAYERS == (
        "code_default",
        "default_yaml",
        "config_file_yaml",
        "environ",
        "dotenv",
        "database",
        "cli",
    )
    assert len(CONFIG_LAYERS) == 7
    assert len(set(CONFIG_LAYERS)) == 7


def test_env_prefix_and_delimiter() -> None:
    """前缀与嵌套分隔符是对外契约，改动会破坏所有部署脚本。"""
    assert ENV_PREFIX == "LOOPFORGE__"
    assert ENV_NESTED_DELIMITER == "__"


def test_project_root_points_to_repo() -> None:
    """``project_root()`` 必须指向包含 ``pyproject.toml`` 的目录。"""
    root = project_root()
    assert root.is_dir()
    assert (root / "pyproject.toml").is_file()
    assert default_config_path().name == "default.yaml"


# ---------------------------------------------------------------------------
# 第 1 层：代码默认值
# ---------------------------------------------------------------------------


def test_layer1_code_defaults() -> None:
    """不加载任何外部来源时，全部取代码内默认值。"""
    settings = _load()
    assert settings.app.name == "loopforge"
    assert settings.app.env == "development"
    assert settings.db.url.startswith("sqlite+aiosqlite://")
    assert settings.providers.default == "mock"
    assert settings.lease.base_ttl_s == 90
    assert settings.memory.max_context_tokens == 128_000


# ---------------------------------------------------------------------------
# 第 2 层：config/default.yaml
# ---------------------------------------------------------------------------


def test_layer2_default_yaml_loads_and_validates() -> None:
    """仓库自带的 ``config/default.yaml`` 必须能被解析并通过校验。"""
    if not default_config_path().is_file():
        pytest.skip("config/default.yaml 不存在（最小化安装）")
    settings = load_settings(
        include_default_yaml=True, environ={}, dotenv_file=_NO_DOTENV
    )
    assert isinstance(settings, Settings)
    assert settings.app.name


# ---------------------------------------------------------------------------
# 第 3 层：显式 YAML 文件
# ---------------------------------------------------------------------------


def test_layer3_config_file_overrides_defaults(tmp_path: Path) -> None:
    """显式 YAML 覆盖代码默认值。"""
    config_file = tmp_path / "custom.yaml"
    config_file.write_text(
        "app:\n  name: from-yaml\ndb:\n  pool_size: 33\n", encoding="utf-8"
    )
    settings = _load(config_file=config_file)
    assert settings.app.name == "from-yaml"
    assert settings.db.pool_size == 33


def test_layer3_missing_explicit_file_raises(tmp_path: Path) -> None:
    """显式指定但不存在的配置文件应当报错，而不是静默忽略。"""
    with pytest.raises(ConfigError):
        _load(config_file=tmp_path / "nope.yaml")


def test_layer3_broken_yaml_raises(tmp_path: Path) -> None:
    """YAML 语法错误必须转成 ConfigError。"""
    config_file = tmp_path / "broken.yaml"
    config_file.write_text("app:\n  name: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        _load(config_file=config_file)


# ---------------------------------------------------------------------------
# 第 4 层：环境变量
# ---------------------------------------------------------------------------


def test_layer4_env_overrides_yaml(tmp_path: Path) -> None:
    """环境变量覆盖 YAML。"""
    config_file = tmp_path / "c.yaml"
    config_file.write_text("app:\n  name: from-yaml\n", encoding="utf-8")
    settings = _load(
        config_file=config_file, environ={"LOOPFORGE__APP__NAME": "from-env"}
    )
    assert settings.app.name == "from-env"


def test_layer4_env_scalar_coercion() -> None:
    """环境变量字符串要能自动转成 bool / int / float / list。"""
    settings = _load(
        environ={
            "LOOPFORGE__DB__POOL_SIZE": "42",
            "LOOPFORGE__DB__ECHO": "true",
            "LOOPFORGE__PROVIDERS__BACKOFF_BASE_S": "2.5",
            "LOOPFORGE__PROVIDERS__FAILOVER_CHAIN": "[mock, openai]",
        }
    )
    assert settings.db.pool_size == 42
    assert settings.db.echo is True
    assert settings.providers.backoff_base_s == pytest.approx(2.5)
    assert settings.providers.failover_chain == ["mock", "openai"]


def test_layer4_env_ignores_foreign_prefix() -> None:
    """非 ``LOOPFORGE__`` 前缀的变量必须被忽略。"""
    settings = _load(environ={"OTHER__APP__NAME": "nope", "PATH": "/usr/bin"})
    assert settings.app.name == "loopforge"


def test_layer4_env_deep_nesting() -> None:
    """三层嵌套 ``A__B__C`` 要能正确还原。"""
    settings = _load(environ={"LOOPFORGE__MEMORY__LAYER_RATIOS__PINNED": "0.15"})
    assert settings.memory.layer_ratios.pinned == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# 第 5 层：.env（按文档，优先级高于真实环境变量）
# ---------------------------------------------------------------------------


def test_layer5_dotenv_overrides_environ(tmp_path: Path) -> None:
    """``.env`` 覆盖真实环境变量 —— 这是 ARCHITECTURE.md 的明文规定。"""
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# 注释行\nexport LOOPFORGE__APP__NAME='from-dotenv'\n", encoding="utf-8"
    )
    settings = _load(
        environ={"LOOPFORGE__APP__NAME": "from-env"}, dotenv_file=dotenv
    )
    assert settings.app.name == "from-dotenv"


def test_layer5_dotenv_quoting_and_comments(tmp_path: Path) -> None:
    """引号剥离、注释跳过、空行跳过。"""
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n"
        "# 这是注释\n"
        'LOOPFORGE__APP__VERSION="9.9.9"\n'
        "LOOPFORGE__DB__POOL_SIZE=7\n"
        "NOT_AN_ASSIGNMENT\n",
        encoding="utf-8",
    )
    settings = _load(dotenv_file=dotenv)
    assert settings.app.version == "9.9.9"
    assert settings.db.pool_size == 7


def test_layer5_missing_dotenv_is_silent(tmp_path: Path) -> None:
    """``.env`` 不存在是常态，不能报错。"""
    settings = _load(dotenv_file=tmp_path / "absent.env")
    assert settings.app.name == "loopforge"


# ---------------------------------------------------------------------------
# 第 6/7 层：数据库热配置与 CLI
# ---------------------------------------------------------------------------


def test_layer6_db_overrides_dotenv(tmp_path: Path) -> None:
    """数据库热配置覆盖 ``.env``。"""
    dotenv = tmp_path / ".env"
    dotenv.write_text("LOOPFORGE__PROVIDERS__DEFAULT=from-dotenv\n", encoding="utf-8")
    settings = _load(
        dotenv_file=dotenv, db_overrides={"providers": {"default": "from-db"}}
    )
    assert settings.providers.default == "from-db"


def test_layer7_cli_wins_over_everything(tmp_path: Path) -> None:
    """CLI 是最后一层，压过其余全部。"""
    config_file = tmp_path / "c.yaml"
    config_file.write_text("app:\n  name: yaml\n", encoding="utf-8")
    dotenv = tmp_path / ".env"
    dotenv.write_text("LOOPFORGE__APP__NAME=dotenv\n", encoding="utf-8")
    settings = _load(
        config_file=config_file,
        environ={"LOOPFORGE__APP__NAME": "env"},
        dotenv_file=dotenv,
        db_overrides={"app": {"name": "db"}},
        cli_overrides={"app": {"name": "cli"}},
    )
    assert settings.app.name == "cli"


def test_deep_merge_keeps_sibling_keys(tmp_path: Path) -> None:
    """覆盖某个叶子不应把兄弟键抹掉。"""
    config_file = tmp_path / "c.yaml"
    config_file.write_text("db:\n  pool_size: 5\n  echo: true\n", encoding="utf-8")
    settings = _load(config_file=config_file, cli_overrides={"db": {"pool_size": 9}})
    assert settings.db.pool_size == 9
    assert settings.db.echo is True


# ---------------------------------------------------------------------------
# 校验失败
# ---------------------------------------------------------------------------


def test_unknown_key_is_rejected() -> None:
    """``extra="forbid"`` 保证拼错的键会被立刻发现。"""
    with pytest.raises(ConfigError):
        _load(cli_overrides={"app": {"nmae": "typo"}})


def test_unknown_top_level_section_is_rejected() -> None:
    """未知顶层节同样拒绝。"""
    with pytest.raises(ConfigError):
        _load(cli_overrides={"not_a_section": {"x": 1}})


def test_out_of_range_value_is_rejected() -> None:
    """越界数值必须被 Field 约束拦住。"""
    with pytest.raises(ConfigError):
        _load(cli_overrides={"db": {"pool_size": 0}})


def test_bad_literal_is_rejected() -> None:
    """Literal 字段只接受白名单取值。"""
    with pytest.raises(ConfigError):
        _load(cli_overrides={"app": {"env": "staging"}})


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------


def test_set_and_get_settings_singleton() -> None:
    """``set_settings`` 注入的对象应当被 ``get_settings`` 原样取回。"""
    injected = _load(cli_overrides={"app": {"name": "singleton"}})
    set_settings(injected)
    try:
        assert get_settings() is injected
        assert get_settings().app.name == "singleton"
    finally:
        set_settings(None)


def test_reload_settings_replaces_singleton() -> None:
    """``reload_settings`` 换掉单例并返回新对象。"""
    set_settings(None)
    try:
        first = reload_settings(
            include_default_yaml=False,
            environ={},
            dotenv_file=_NO_DOTENV,
            cli_overrides={"app": {"name": "a"}},
        )
        second = reload_settings(
            include_default_yaml=False,
            environ={},
            dotenv_file=_NO_DOTENV,
            cli_overrides={"app": {"name": "b"}},
        )
        assert first is not second
        assert get_settings() is second
        assert get_settings().app.name == "b"
    finally:
        set_settings(None)


def test_dump_is_json_serializable() -> None:
    """``dump()`` 结果要能直接塞进 ``runs.config_snapshot``。"""
    import json

    payload = _load().dump()
    assert isinstance(payload, dict)
    assert json.loads(json.dumps(payload))["app"]["name"] == "loopforge"


# ---------------------------------------------------------------------------
# 子树领域逻辑
# ---------------------------------------------------------------------------


def test_roles_for_role_lookup() -> None:
    """角色查找大小写不敏感，未知角色抛 ConfigError。"""
    roles = RolesSettings()
    assert roles.for_role("Planner") is roles.planner
    assert roles.for_role("  coder ") is roles.coder
    assert roles.summarizer.model == "mock-small"
    with pytest.raises(ConfigError):
        roles.for_role("architect")


def test_memory_layer_ratios_sum_to_one() -> None:
    """五层配额之和必须为 1.0，否则上下文装配会溢出或浪费。"""
    memory = MemorySettings()
    assert memory.layer_ratios.total() == pytest.approx(1.0)


def test_memory_budget_tokens() -> None:
    """预算 token = 上限 × 使用率 − 预留输出。"""
    memory = MemorySettings(
        max_context_tokens=100_000, usage_ratio=0.6, reserve_output_tokens=8_000
    )
    assert memory.budget_tokens == 52_000


def test_lease_ttl_cross_field_validation() -> None:
    """``max_ttl_s`` 不能小于 ``base_ttl_s``。"""
    assert LeaseSettings(base_ttl_s=60, max_ttl_s=600).max_ttl_s == 600
    with pytest.raises(ValueError, match="max_ttl_s"):
        LeaseSettings(base_ttl_s=600, max_ttl_s=60)


def test_budget_threshold_order_validation() -> None:
    """必须满足 ``warn_at <= throttle_at <= halt_at``。"""
    assert BudgetSettings(warn_at=0.5, throttle_at=0.8, halt_at=1.0).halt_at == 1.0
    with pytest.raises(ValueError, match="warn_at"):
        BudgetSettings(warn_at=0.9, throttle_at=0.5, halt_at=1.0)


def test_validate_assignment_is_on() -> None:
    """赋值时重新校验，避免运行期被改成非法值。"""
    settings = _load()
    with pytest.raises(ValueError):
        settings.db.pool_size = -1
