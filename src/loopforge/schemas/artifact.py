"""Artifact DTO。

对应模型：:mod:`loopforge.models.artifact`。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from loopforge.constants import MAX_ARTIFACT_BYTES
from loopforge.schemas.common import LFModel, ORMModel, PageQuery

__all__ = [
    "ARTIFACT_KINDS",
    "SHA256_PATTERN",
    "ArtifactCreate",
    "ArtifactRead",
    "ArtifactQuery",
    "ArtifactDownload",
]

#: 产物种类。
ARTIFACT_KINDS: tuple[str, ...] = (
    "log",
    "diff",
    "report",
    "coverage",
    "screenshot",
    "bundle",
    "other",
)

#: 64 位小写 hex 的 sha256。
SHA256_PATTERN: re.Pattern[str] = re.compile(r"^[0-9a-f]{64}$")

#: 允许的存储 scheme。
_STORAGE_SCHEMES: tuple[str, ...] = ("file://", "s3://", "gs://", "http://", "https://")


class ArtifactCreate(LFModel):
    """登记一件产物。

    .. note::
       本 DTO **不接受二进制内容** —— 产物本体先由调用方写入存储，再把
       ``storage_uri`` + ``sha256`` 登记进来。把 blob 塞进关系库是长跑系统
       最常见的自杀方式之一。
    """

    run_id: str = Field(min_length=1, max_length=26)
    task_id: str | None = Field(default=None, max_length=26)
    step_id: str | None = Field(default=None, max_length=26)
    kind: str = Field(default="other")
    storage_uri: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(default=0, ge=0, le=MAX_ARTIFACT_BYTES)
    mime_type: str = Field(default="application/octet-stream", max_length=200)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        """校验产物种类。

        :raises ValueError: 种类未知。
        """
        normalized = value.strip().lower()
        if normalized not in ARTIFACT_KINDS:
            raise ValueError(f"kind 必须是 {list(ARTIFACT_KINDS)} 之一")
        return normalized

    @field_validator("sha256")
    @classmethod
    def _check_sha(cls, value: str) -> str:
        """校验 sha256 形态。

        :raises ValueError: 不是 64 位小写 hex。
        """
        normalized = value.strip().lower()
        if not SHA256_PATTERN.match(normalized):
            raise ValueError("sha256 必须是 64 位小写十六进制字符串")
        return normalized

    @field_validator("storage_uri")
    @classmethod
    def _check_uri(cls, value: str) -> str:
        """校验存储 URI 的 scheme。

        :raises ValueError: scheme 不受支持。
        """
        normalized = value.strip()
        if not normalized.startswith(_STORAGE_SCHEMES):
            raise ValueError(f"storage_uri 必须以 {list(_STORAGE_SCHEMES)} 之一开头")
        return normalized


class ArtifactRead(ORMModel):
    """产物读模型。"""

    id: str
    run_id: str
    task_id: str | None = None
    step_id: str | None = None
    kind: str
    storage_uri: str
    sha256: str
    size_bytes: int = 0
    mime_type: str = "application/octet-stream"
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @property
    def size_human(self) -> str:
        """人类可读的体积字符串。"""
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024.0 or unit == "GB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024.0
        return f"{size:.1f} GB"


class ArtifactQuery(PageQuery):
    """产物列表查询参数。"""

    run_id: str | None = Field(default=None, max_length=26)
    task_id: str | None = Field(default=None, max_length=26)
    kind: str | None = Field(default=None, max_length=64)


class ArtifactDownload(LFModel):
    """产物下载响应（预签名 URL 或本地直链）。"""

    id: str
    url: str
    expires_at: datetime | None = Field(default=None)
    sha256: str = ""
    size_bytes: int = Field(default=0, ge=0)
