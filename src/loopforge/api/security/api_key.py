"""API Key 认证 —— ``Authorization: Bearer lf_<prefix>_<secret>``。

Key 格式（``TASK_BREAKDOWN.md`` §11.8）：``lf_<prefix>_<secret>``。
库里只存 ``prefix`` 与 ``hashed_secret``（SHA-256），明文仅签发时返回一次。
"""

from __future__ import annotations

from datetime import UTC, datetime

from loopforge.core.clock import get_clock
from loopforge.core.errors import AuthError
from loopforge.core.hashing import sha256_hex
from loopforge.core.logging import get_logger
from loopforge.db.session import read_session
from loopforge.db.repositories.system_repo import ApiKeyRepository
from loopforge.models.auth import ApiKey, User
from loopforge.schemas.auth import Principal

__all__ = ["ApiKeyAuthenticator", "parse_api_key", "hash_secret"]

_logger = get_logger(__name__)

API_KEY_PREFIX: str = "lf"


def hash_secret(secret: str) -> str:
    """计算密钥哈希（存库用）。"""
    return sha256_hex(secret)


def parse_api_key(token: str) -> tuple[str, str]:
    """解析 ``lf_<prefix>_<secret>``。

    :raises AuthError: 格式非法。
    :returns: ``(prefix, secret)``。
    """
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != API_KEY_PREFIX:
        raise AuthError("API Key 格式非法：应为 lf_<prefix>_<secret>")
    return parts[1], parts[2]


class ApiKeyAuthenticator:
    """基于 API Key 的认证器。

    用法（依赖注入）::

        auth = ApiKeyAuthenticator()
        principal = await auth.authenticate(token)
    """

    async def authenticate(self, token: str) -> Principal:
        """校验 Bearer token 并返回主体。

        :raises AuthError: 无效 / 过期 / 吊销。
        """
        prefix, secret = parse_api_key(token)
        async with read_session() as session:
            repo = ApiKeyRepository(session)
            key = await repo.get_by_prefix(prefix)
            if key is None:
                raise AuthError("API Key 无效", details={"prefix": prefix})
            if key.revoked:
                raise AuthError("API Key 已吊销", details={"key_id": key.id})
            if key.expires_at is not None and get_clock().now() >= key.expires_at:
                raise AuthError("API Key 已过期", details={"key_id": key.id})
            if key.hashed_secret != hash_secret(secret):
                raise AuthError("API Key 无效", details={"prefix": prefix})
            await repo.touch_last_used(key.id)
            role = "viewer"
            username: str | None = None
            if key.user_id is not None and key.user is not None:
                role = key.user.role
                username = key.user.username
            return Principal(
                subject_id=key.id,
                subject_type="api_key",
                username=username,
                role=role,
                scopes=list(key.scopes or []),
            )
