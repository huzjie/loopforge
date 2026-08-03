"""Prompt 加载与渲染 —— 版本化 + 内容 hash（§8.3-3 强制约定）。

设计要点
--------
1. **数据文件独立**：prompt 模板放在仓库根 ``prompts/*.md``（与 ``config/``
   同级的数据资产目录），不硬编码进 Python 源码，便于非开发人员编辑。
2. **版本号 front matter**：每个 ``.md`` 头部 ``version: vN``；加载器解析并
   与内容 hash 一并返回。WP4 写入 ``LlmCall.prompt_version`` /
   ``prompt_hash``，A/B 与回归定位全靠它。
3. **Jinja2 渲染**：模板内 ``{{ variable }}`` 用 :mod:`jinja2` 渲染；
   未提供的变量保持原样（不抛异常），由 :func:`validate_placeholders`
   在测试中兜底校验。
4. **测试友好**：``LOOPFORGE_PROMPTS_DIR`` 环境变量可覆盖 prompts 目录，
   单测无需复制仓库级数据文件。

契约（§8.1.1 ``roles/prompts.py``）::

    load_prompt(name) -> PromptTemplate
    render_prompt(name, **variables) -> RenderedPrompt
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, meta, StrictUndefined

from loopforge.core.config import project_root
from loopforge.core.hashing import sha256_hex
from loopforge.core.logging import get_logger
from loopforge.roles.base import RolePromptMissingError

__all__ = [
    "PROMPTS_DIR",
    "PROMPT_NAMES",
    "PromptTemplate",
    "RenderedPrompt",
    "load_prompt",
    "render_prompt",
    "prompt_versions",
    "prompt_hash",
    "validate_placeholders",
    "get_prompts_dir",
]

_log = get_logger(__name__)

#: 全部 prompt 数据文件名（§8.1.4 冻结清单）。
PROMPT_NAMES: Final[tuple[str, ...]] = (
    "planner.md",
    "coder.md",
    "reviewer.md",
    "tester.md",
    "summarizer.md",
    "compress_milestone.md",
    "pr_description.md",
    "constraint_check.md",
    "evolution_normalize.md",
    "fix_from_failure.md",
)

#: front matter 版本行：``version: v1``。
_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"^\s*version\s*:\s*([A-Za-z0-9_.-]+)\s*$", re.MULTILINE)

_cache: dict[str, "PromptTemplate"] = {}


def get_prompts_dir() -> Path:
    """定位 prompts 数据目录。

    优先级：
    1. ``$LOOPFORGE_PROMPTS_DIR`` 环境变量（测试 / 部署覆盖）；
    2. 仓库根 ``prompts/``（``core.config.project_root()``）。
    """
    override = os.environ.get("LOOPFORGE_PROMPTS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return project_root() / "prompts"


#: 当前生效的 prompts 目录（模块加载时解析，测试可通过 env 覆盖后调用
#: :func:`reset_prompt_cache` 重载）。
PROMPTS_DIR: Path = get_prompts_dir()


def reset_prompt_cache() -> None:
    """清空已加载模板缓存（测试改 ``LOOPFORGE_PROMPTS_DIR`` 后调用）。"""
    _cache.clear()


@dataclass(frozen=True)
class PromptTemplate:
    """一个已加载的 Prompt 模板。

    Attributes:
        name: 模板文件名（``planner.md``）。
        version: front matter 版本号（缺省 ``"v0"``）。
        content: 完整原文。
        body: 去掉 front matter 后的正文。
        hash: 正文的 sha256（版本号变更但正文未变时 hash 不变，便于精确对比）。
    """

    name: str
    version: str = "v0"
    content: str = ""
    body: str = ""
    hash: str = ""

    @property
    def title(self) -> str:
        """模板展示名（去掉扩展名）。"""
        return self.name.removesuffix(".md")

    def render(self, **variables: Any) -> "RenderedPrompt":
        """渲染当前模板。"""
        return render_prompt(self.name, **variables)


@dataclass(frozen=True)
class RenderedPrompt:
    """一次渲染的产物 —— 记录版本与 hash，供 ``LlmCall`` 落库。"""

    name: str
    text: str
    version: str = "v0"
    hash: str = ""
    variables: dict[str, Any] = field(default_factory=dict)


def load_prompt(name: str) -> PromptTemplate:
    """加载一个 Prompt 模板（带进程级缓存）。

    Args:
        name: 模板文件名，如 ``planner.md``。

    Returns:
        解析后的 :class:`PromptTemplate`。

    Raises:
        RolePromptMissingError: 文件不存在或为空（``LF-ROLE-1603``）。
    """
    if name in _cache:
        return _cache[name]
    path = PROMPTS_DIR / name
    if not path.is_file():
        raise RolePromptMissingError(
            f"Prompt 模板缺失：{path}",
            details={"name": name, "prompts_dir": str(PROMPTS_DIR)},
        )
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise RolePromptMissingError(
            f"Prompt 模板为空：{path}", details={"name": name}
        )
    match = _VERSION_RE.search(content)
    version = match.group(1).strip() if match else "v0"
    body = _VERSION_RE.sub("", content, count=1).strip()
    template = PromptTemplate(
        name=name,
        version=version,
        content=content,
        body=body,
        hash=sha256_hex(body),
    )
    _cache[name] = template
    return template


def render_prompt(name: str, **variables: Any) -> RenderedPrompt:
    """渲染模板。

    Args:
        name: 模板文件名。
        **variables: Jinja2 上下文变量。

    Returns:
        :class:`RenderedPrompt`。

    Raises:
        RolePromptMissingError: 模板缺失。
    """
    template = load_prompt(name)
    env = Environment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    try:
        jinja = env.from_string(template.body)
        text = jinja.render(**variables)
    except Exception as exc:  # noqa: BLE001 - 渲染错误统一转 RolePromptMissingError
        raise RolePromptMissingError(
            f"Prompt 渲染失败：{name} — {exc}",
            details={"name": name, "variables": sorted(variables)},
        ) from exc
    return RenderedPrompt(
        name=name,
        text=text,
        version=template.version,
        hash=template.hash,
        variables=dict(variables),
    )


def prompt_versions() -> dict[str, str]:
    """全部模板的版本号（``{文件名: 版本号}``），供巡检与 CI 校验。"""
    return {name: load_prompt(name).version for name in PROMPT_NAMES}


def prompt_hash(name: str) -> str:
    """单个模板的正文 hash。"""
    return load_prompt(name).hash


def validate_placeholders(name: str, required: list[str]) -> list[str]:
    """校验模板使用了哪些占位符（``{{ var }}``）。

    Args:
        name: 模板文件名。
        required: 期望出现的占位符名列表。

    Returns:
        实际出现的占位符名（按 ``required`` 顺序过滤）。
    """
    template = load_prompt(name)
    env = Environment(autoescape=False)
    try:
        ast = env.parse(template.body)
        found = meta.find_undeclared_variables(ast)
    except Exception:  # noqa: BLE001 - 解析失败按未发现处理
        return []
    return [var for var in required if var in found]
