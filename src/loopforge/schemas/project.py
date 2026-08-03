"""Project / Repo DTO。

对应模型：:mod:`loopforge.models.project`。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from loopforge.schemas.common import LFModel, ORMModel

__all__ = [
    "SLUG_PATTERN",
    "ProjectCreate",
    "ProjectUpdate",
    "ProjectRead",
    "RepoCreate",
    "RepoUpdate",
    "RepoRead",
    "ProjectDetail",
]

#: slug 规则：小写字母数字与连字符，不能以连字符开头 / 结尾。
SLUG_PATTERN: re.Pattern[str] = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")

#: 支持的代码仓 provider。
_REPO_PROVIDERS: frozenset[str] = frozenset({"local", "github"})


class ProjectCreate(LFModel):
    """创建项目。"""

    slug: str = Field(min_length=1, max_length=64, description="URL 友好唯一标识")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, value: str) -> str:
        """校验 slug 形态。

        :raises ValueError: slug 不合法。
        """
        normalized = value.strip().lower()
        if not SLUG_PATTERN.match(normalized):
            raise ValueError(
                "slug 只能包含小写字母、数字与连字符，且不能以连字符开头或结尾"
            )
        return normalized


class ProjectUpdate(LFModel):
    """更新项目（全部字段可选，仅更新给出的字段）。"""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    settings: dict[str, Any] | None = Field(default=None)

    def changed_fields(self) -> dict[str, Any]:
        """返回本次真正要更新的字段。

        :returns: 非 ``None`` 字段构成的字典（可能为空）。
        """
        return self.model_dump(exclude_none=True)


class RepoCreate(LFModel):
    """在项目下登记一个代码仓。"""

    provider: str = Field(default="local", max_length=32)
    remote_url: str | None = Field(default=None, max_length=500)
    default_branch: str = Field(default="main", min_length=1, max_length=200)
    credentials_ref: str | None = Field(default=None, max_length=200)
    local_path: str | None = Field(default=None, max_length=1000)
    labels_config: dict[str, Any] = Field(default_factory=dict)
    webhook_enabled: bool = Field(default=False)

    @field_validator("provider")
    @classmethod
    def _check_provider(cls, value: str) -> str:
        """校验 provider 取值。

        :raises ValueError: provider 不在支持列表。
        """
        normalized = value.strip().lower()
        if normalized not in _REPO_PROVIDERS:
            raise ValueError(f"provider 必须是 {sorted(_REPO_PROVIDERS)} 之一")
        return normalized

    @model_validator(mode="after")
    def _check_locator(self) -> Self:
        """``github`` 必须有 ``remote_url``，``local`` 必须有 ``local_path``。

        :raises ValueError: 定位信息缺失。
        """
        if self.provider == "github" and not self.remote_url:
            raise ValueError("provider=github 时必须提供 remote_url")
        if self.provider == "local" and not self.local_path:
            raise ValueError("provider=local 时必须提供 local_path")
        if self.webhook_enabled and self.provider != "github":
            raise ValueError("只有 provider=github 才支持 webhook")
        return self


class RepoUpdate(LFModel):
    """更新代码仓配置。"""

    default_branch: str | None = Field(default=None, min_length=1, max_length=200)
    credentials_ref: str | None = Field(default=None, max_length=200)
    local_path: str | None = Field(default=None, max_length=1000)
    labels_config: dict[str, Any] | None = Field(default=None)
    webhook_enabled: bool | None = Field(default=None)

    def changed_fields(self) -> dict[str, Any]:
        """返回本次真正要更新的字段。"""
        return self.model_dump(exclude_none=True)


class RepoRead(ORMModel):
    """代码仓读模型。

    .. note::
       ``webhook_secret_enc`` **不在** DTO 里 —— 密文也不该出现在 API 响应中。
    """

    id: str
    project_id: str
    provider: str
    remote_url: str | None = None
    default_branch: str = "main"
    credentials_ref: str | None = None
    local_path: str | None = None
    labels_config: dict[str, Any] = Field(default_factory=dict)
    webhook_enabled: bool = False
    created_at: datetime
    updated_at: datetime


class ProjectRead(ORMModel):
    """项目读模型（不含子资源）。"""

    id: str
    slug: str
    name: str
    description: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectRead):
    """项目详情（带代码仓列表与统计）。"""

    repos: list[RepoRead] = Field(default_factory=list)
    run_count: int = Field(default=0, ge=0)
    active_run_count: int = Field(default=0, ge=0)
