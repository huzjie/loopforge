"""RBAC —— admin / operator / viewer 三级权限。

权限矩阵（``ARCHITECTURE.md`` §11）：A=admin，O=operator，V=viewer。
``require_role`` 返回主体本身，便于路由继续使用；不满足权限抛 ``LF-AUTH-0102``。
"""

from __future__ import annotations

from loopforge.core.errors import AuthError
from loopforge.schemas.auth import Principal

__all__ = ["require_role", "ROLE_HIERARCHY"]

#: 角色层级：数字越大权限越高。
ROLE_HIERARCHY: dict[str, int] = {"viewer": 1, "operator": 2, "admin": 3}


def require_role(required: str, principal: Principal) -> Principal:
    """校验主体角色是否满足要求。

    :param required: viewer / operator / admin。
    :param principal: 当前主体。
    :raises AuthError: 权限不足（``LF-AUTH-0102``）。
    """
    actual = ROLE_HIERARCHY.get(principal.role, 0)
    needed = ROLE_HIERARCHY.get(required, 0)
    if actual < needed:
        raise AuthError(
            f"权限不足：需要 {required}，当前为 {principal.role}",
            details={"required": required, "actual": principal.role},
        )
    return principal
