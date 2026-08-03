"""认证与授权 DTO。

**安全铁律**：

1. 明文密码 / 明文密钥只能出现在**入参**模型里，读模型一律不含。
2. API Key 的完整值只在**签发那一次**返回（:class:`ApiKeyIssued`），
   之后永远只能看到 ``prefix``。这是不可逆设计，不是不便。
3. 任何模型的 ``__repr__`` 都不会打印敏感字段 —— 依赖 ``repr=False``。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from loopforge.schemas.common import LFModel, ORMModel

__all__ = [
    "USER_ROLES",
    "API_SCOPES",
    "USERNAME_MIN_LENGTH",
    "PASSWORD_MIN_LENGTH",
    "UserCreate",
    "UserUpdate",
    "UserRead",
    "UserQuery",
    "PasswordChangeRequest",
    "LoginRequest",
    "TokenResponse",
    "ApiKeyCreate",
    "ApiKeyRead",
    "ApiKeyIssued",
    "ApiKeyRevokeRequest",
    "Principal",
]

#: 用户角色。
USER_ROLES: tuple[str, ...] = ("admin", "operator", "viewer")

#: API Key 可授予的权限范围。
API_SCOPES: tuple[str, ...] = (
    "runs:read",
    "runs:write",
    "runs:control",
    "tasks:read",
    "tasks:write",
    "events:read",
    "memory:read",
    "memory:write",
    "policy:read",
    "policy:write",
    "approvals:read",
    "approvals:decide",
    "admin:all",
)

#: 用户名最短长度。
USERNAME_MIN_LENGTH: int = 3

#: 密码最短长度。自托管环境同样会被爆破，别侥幸。
PASSWORD_MIN_LENGTH: int = 12


def _validate_scopes(scopes: list[str]) -> list[str]:
    """校验并规范化 scope 列表（去重 + 白名单）。"""
    normalized: list[str] = []
    for scope in scopes:
        item = scope.strip().lower()
        if not item:
            continue
        if item not in API_SCOPES:
            raise ValueError(f"未知 scope：{scope!r}，合法取值为 {API_SCOPES}")
        if item not in normalized:
            normalized.append(item)
    return normalized


class UserCreate(LFModel):
    """创建用户。"""

    username: str = Field(min_length=USERNAME_MIN_LENGTH, max_length=100, description="用户名")
    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH, max_length=200, repr=False, description="明文密码"
    )
    role: str = Field(default="viewer", max_length=32, description="角色")
    active: bool = Field(default=True, description="是否启用")

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        """用户名只允许字母数字与 ``._-``。"""
        normalized = value.strip().lower()
        if not normalized.replace(".", "").replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"用户名只能包含字母、数字与 . _ -，收到 {value!r}")
        return normalized

    @field_validator("role")
    @classmethod
    def _check_role(cls, value: str) -> str:
        """角色必须在白名单内。"""
        normalized = value.strip().lower()
        if normalized not in USER_ROLES:
            raise ValueError(f"role 必须是 {USER_ROLES} 之一，收到 {value!r}")
        return normalized

    @model_validator(mode="after")
    def _check_password_strength(self) -> UserCreate:
        """密码不得包含用户名，且必须混合字符类型。"""
        if self.username in self.password.lower():
            raise ValueError("密码不得包含用户名")
        has_alpha = any(char.isalpha() for char in self.password)
        has_digit = any(char.isdigit() for char in self.password)
        if not (has_alpha and has_digit):
            raise ValueError("密码必须同时包含字母与数字")
        return self


class UserUpdate(LFModel):
    """更新用户（不含改密码，改密走 :class:`PasswordChangeRequest`）。"""

    role: str | None = Field(default=None, max_length=32, description="新角色")
    active: bool | None = Field(default=None, description="启停")

    @field_validator("role")
    @classmethod
    def _check_role(cls, value: str | None) -> str | None:
        """角色必须在白名单内。"""
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in USER_ROLES:
            raise ValueError(f"role 必须是 {USER_ROLES} 之一，收到 {value!r}")
        return normalized

    def changed_fields(self) -> dict[str, object]:
        """返回本次实际提交的字段。"""
        return self.model_dump(exclude_none=True)


class UserRead(ORMModel):
    """用户读模型 —— 不含任何口令信息。"""

    id: str = Field(description="用户 ID")
    username: str = Field(description="用户名")
    role: str = Field(default="viewer", description="角色")
    active: bool = Field(default=True, description="是否启用")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")

    @property
    def is_admin(self) -> bool:
        """是否管理员。"""
        return self.role == "admin"

    @property
    def can_write(self) -> bool:
        """是否具备写权限。"""
        return self.role in ("admin", "operator")


class UserQuery(LFModel):
    """用户查询条件。"""

    username: str | None = Field(default=None, max_length=100, description="按用户名过滤")
    role: str | None = Field(default=None, max_length=32, description="按角色过滤")
    active_only: bool = Field(default=True, description="只看启用的")


class PasswordChangeRequest(LFModel):
    """修改密码。"""

    old_password: str = Field(min_length=1, max_length=200, repr=False, description="原密码")
    new_password: str = Field(
        min_length=PASSWORD_MIN_LENGTH, max_length=200, repr=False, description="新密码"
    )

    @model_validator(mode="after")
    def _check_different(self) -> PasswordChangeRequest:
        """新旧密码必须不同。"""
        if self.old_password == self.new_password:
            raise ValueError("新密码不能与原密码相同")
        return self


class LoginRequest(LFModel):
    """登录请求。"""

    username: str = Field(min_length=1, max_length=100, description="用户名")
    password: str = Field(min_length=1, max_length=200, repr=False, description="密码")

    @field_validator("username")
    @classmethod
    def _normalize(cls, value: str) -> str:
        """统一小写。"""
        return value.strip().lower()


class TokenResponse(LFModel):
    """登录成功后的令牌。"""

    access_token: str = Field(repr=False, description="访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    expires_in: int = Field(default=3_600, ge=1, description="有效期（秒）")
    user: UserRead = Field(description="用户信息")


class ApiKeyCreate(LFModel):
    """签发一个 API Key。"""

    name: str = Field(min_length=1, max_length=200, description="Key 名称")
    user_id: str | None = Field(default=None, max_length=26, description="归属用户")
    scopes: list[str] = Field(default_factory=list, description="权限范围")
    ttl_days: int | None = Field(default=None, ge=1, le=3_650, description="有效天数，省略为永久")

    @field_validator("scopes")
    @classmethod
    def _check_scopes(cls, value: list[str]) -> list[str]:
        """校验 scope 白名单。"""
        return _validate_scopes(value)

    @model_validator(mode="after")
    def _check_scopes_present(self) -> ApiKeyCreate:
        """不允许签发零权限的 Key —— 那只会制造排障噪音。"""
        if not self.scopes:
            raise ValueError("scopes 不能为空，至少授予一项权限")
        return self


class ApiKeyRead(ORMModel):
    """API Key 读模型 —— 只暴露前缀。"""

    id: str = Field(description="Key ID")
    prefix: str = Field(description="Key 前缀（可公开）")
    name: str = Field(default="", description="名称")
    scopes: list[str] = Field(default_factory=list, description="权限范围")
    user_id: str | None = Field(default=None, description="归属用户")
    expires_at: datetime | None = Field(default=None, description="过期时间")
    last_used_at: datetime | None = Field(default=None, description="最后使用时间")
    revoked: bool = Field(default=False, description="是否已吊销")
    created_at: datetime = Field(description="创建时间")

    def is_valid(self, now: datetime) -> bool:
        """当前时刻是否仍可用。"""
        if self.revoked:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        return True

    @property
    def masked(self) -> str:
        """用于展示的掩码形式。"""
        return f"{self.prefix}...{'*' * 8}"


class ApiKeyIssued(LFModel):
    """签发结果 —— ``secret`` 只在此刻出现一次。"""

    key: ApiKeyRead = Field(description="Key 元信息")
    secret: str = Field(repr=False, description="完整密钥明文，仅此一次可见")

    @property
    def warning(self) -> str:
        """给调用方的提示语。"""
        return "请立即保存该密钥，服务端只存哈希，关闭后无法再次查看。"


class ApiKeyRevokeRequest(LFModel):
    """吊销一个 Key。"""

    key_id: str = Field(min_length=1, max_length=26, description="Key ID")
    reason: str = Field(default="", max_length=1_000, description="吊销原因")


class Principal(LFModel):
    """经过认证的调用主体 —— 贯穿请求上下文。"""

    subject_id: str = Field(description="主体 ID（用户 ID 或 Key ID）")
    subject_type: str = Field(default="user", max_length=16, description="主体类型：user/api_key")
    username: str | None = Field(default=None, description="用户名")
    role: str = Field(default="viewer", description="角色")
    scopes: list[str] = Field(default_factory=list, description="权限范围")

    @field_validator("subject_type")
    @classmethod
    def _check_subject_type(cls, value: str) -> str:
        """主体类型白名单。"""
        allowed = ("user", "api_key", "system")
        normalized = value.strip().lower()
        if normalized not in allowed:
            raise ValueError(f"subject_type 必须是 {allowed} 之一，收到 {value!r}")
        return normalized

    def has_scope(self, scope: str) -> bool:
        """是否具备某项权限；``admin:all`` 视为全通。"""
        if "admin:all" in self.scopes or self.role == "admin":
            return True
        return scope in self.scopes

    @classmethod
    def system(cls) -> Principal:
        """内部系统调用主体（对账器、调度器等）。"""
        return cls(
            subject_id="system",
            subject_type="system",
            username="system",
            role="admin",
            scopes=["admin:all"],
        )
