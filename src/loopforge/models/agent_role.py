"""AgentRole / ProviderConfig ORM 模型 —— 角色与模型供应商绑定。

对应表：``agent_roles``、``provider_configs``（``docs/ARCHITECTURE.md`` §5.1）。

.. warning::
   ``api_key_enc`` 存 Fernet 密文，密钥来自
   ``LOOPFORGE__SECURITY__ENCRYPTION_KEY``。**明文 API Key 永不落库、永不进日志**
   （:mod:`loopforge.core.redaction` 会兜底拦截）。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from loopforge.constants import ID_COLUMN_LENGTH
from loopforge.core.enums import RoleName
from loopforge.core.ids import new_ulid
from loopforge.db.base import Base
from loopforge.db.mixins import utcnow
from loopforge.db.types import JsonType, StrEnumType, UtcDateTime

if TYPE_CHECKING:
    pass

__all__ = ["ProviderConfig", "AgentRole"]


class ProviderConfig(Base):
    """一个模型供应商端点配置（OpenAI 兼容 / Anthropic / Ollama / …）。"""

    __tablename__ = "provider_configs"
    __table_args__ = (
        CheckConstraint("priority >= 0", name="ck_provider_configs_priority_non_negative"),
        UniqueConstraint("name", name="uq_provider_name"),
        Index("ix_provider_enabled_priority", "enabled", "priority"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="openai_compatible",
        doc="openai_compatible / anthropic / ollama / custom",
    )
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key_enc: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Fernet 密文；明文永不落库"
    )
    default_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    options: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict, doc="超时、代理、额外请求头等"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, doc="数值越小越优先被选中"
    )
    failover_to: Mapped[str | None] = mapped_column(
        String(64), nullable=True, doc="故障转移目标 provider 的 name"
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    roles: Mapped[list[AgentRole]] = relationship(
        back_populates="provider_config", lazy="noload"
    )


class AgentRole(Base):
    """一个智能体角色的模型与工具绑定。"""

    __tablename__ = "agent_roles"
    __table_args__ = (
        CheckConstraint(
            "temperature >= 0.0 AND temperature <= 2.0",
            name="ck_agent_roles_temperature_range",
        ),
        CheckConstraint("max_tokens > 0", name="ck_agent_roles_max_tokens_positive"),
        UniqueConstraint("name", name="uq_agent_roles_name"),
    )

    id: Mapped[str] = mapped_column(
        String(ID_COLUMN_LENGTH), primary_key=True, default=new_ulid
    )
    name: Mapped[RoleName] = mapped_column(StrEnumType(RoleName), nullable=False)
    provider_config_id: Mapped[str | None] = mapped_column(
        String(ID_COLUMN_LENGTH),
        ForeignKey("provider_configs.id", ondelete="SET NULL"),
        nullable=True,
    )
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4096)
    allowed_tools: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, default=list, doc="白名单工具名；空列表表示禁用全部工具"
    )
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    provider_config: Mapped[ProviderConfig | None] = relationship(
        back_populates="roles", lazy="joined"
    )
