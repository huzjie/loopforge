"""策略加载器 —— 从 YAML 文件 / 字符串加载策略文档。

来源：``docs/TASK_BREAKDOWN.md`` §7.1.2 ``policy/loader.py``。

默认策略目录：``config/policies/``（default / strict / permissive 三档）。
"""

from __future__ import annotations

from pathlib import Path

from loopforge.core.config import project_root
from loopforge.core.errors import ConfigError
from loopforge.governance.policy.dsl import PolicyDocument, parse_policy_yaml

__all__ = ["PolicyLoader", "POLICY_DIR", "policy_path"]

#: 默认策略目录。
POLICY_DIR: Path = project_root() / "config" / "policies"


def policy_path(name: str = "default") -> Path:
    """取策略文件路径（自动补 ``.yaml``）。

    :param name: 策略名（``default`` / ``strict`` / ``permissive`` 或文件名）。
    :returns: 绝对路径。
    """
    candidate = Path(name)
    if candidate.is_absolute() or candidate.suffix in (".yaml", ".yml"):
        return candidate
    return POLICY_DIR / f"{name}.yaml"


class PolicyLoader:
    """策略加载器。

    :param base_dir: 策略目录；``None`` 用仓库默认 ``config/policies``。
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        """初始化加载器。"""
        self.base_dir: Path = Path(base_dir) if base_dir is not None else POLICY_DIR

    def load_file(self, path: Path | str) -> PolicyDocument:
        """从文件加载。

        :param path: 文件路径。
        :returns: 解析后的策略文档。
        :raises ConfigError: 文件不存在或解析失败。
        """
        candidate = Path(path)
        if not candidate.is_file():
            raise ConfigError(f"策略文件不存在：{candidate}", details={"path": str(candidate)})
        try:
            return parse_policy_yaml(candidate.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ConfigError(
                f"策略文件解析失败：{candidate} — {exc}",
                details={"path": str(candidate), "error": str(exc)},
            ) from exc

    def load(self, name: str = "default") -> PolicyDocument:
        """按名字加载（``default`` / ``strict`` / ``permissive``）。

        :param name: 策略名。
        :returns: 策略文档。
        :raises ConfigError: 文件缺失或非法。
        """
        return self.load_file(policy_path(name))

    def load_text(self, text: str) -> PolicyDocument:
        """从字符串加载。

        :param text: YAML 文本。
        :returns: 策略文档。
        :raises ValueError: 解析失败。
        """
        return parse_policy_yaml(text)

    def list_names(self) -> list[str]:
        """列出可用策略名。"""
        if not self.base_dir.is_dir():
            return []
        return sorted(
            path.stem
            for path in self.base_dir.glob("*.yaml")
            if path.is_file() and not path.name.startswith("_")
        )
