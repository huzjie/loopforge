"""API 依赖注入 —— session / UoW / 服务单例 / 认证主体。

所有路由通过这里拿依赖，业务逻辑全部下沉到 service 层，
路由层只做参数校验 + 调服务 + 组装响应（§9.3 约定 4）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from loopforge.core.errors import AuthError
from loopforge.core.logging import get_logger
from loopforge.db.session import get_session
from loopforge.db.uow import UnitOfWork
from loopforge.schemas.auth import Principal

from loopforge.api.security.api_key import ApiKeyAuthenticator
from loopforge.api.security.rbac import require_role

__all__ = [
    "get_db_session",
    "get_uow",
    "get_principal",
    "get_authenticator",
    "require_admin",
    "require_operator",
    "require_viewer",
    "get_service",
    "PublicPrincipal",
]

_logger = get_logger(__name__)

#: 公开端点（无认证）使用的主体。
PUBLIC_PRINCIPAL = Principal(
    subject_id="anonymous",
    subject_type="system",
    username="anonymous",
    role="viewer",
    scopes=[],
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：一个请求一个数据库 session。"""
    async for session in get_session():
        yield session


async def get_uow() -> AsyncIterator[UnitOfWork]:
    """FastAPI 依赖：请求级 UnitOfWork（写路径统一用它）。"""
    async with UnitOfWork() as uow:
        yield uow


def get_authenticator(request: Request) -> ApiKeyAuthenticator:
    """取应用级 API Key 认证器（惰性单例）。"""
    auth = getattr(request.app.state, "api_key_authenticator", None)
    if auth is None:
        auth = ApiKeyAuthenticator()
        request.app.state.api_key_authenticator = auth
    return auth


async def get_principal(
    request: Request,
    authenticator: ApiKeyAuthenticator = Depends(get_authenticator),
) -> Principal:
    """FastAPI 依赖：解析 Authorization 头得到当前主体。

    公开端点（健康检查 / 登录 / webhook / metrics / docs）无需认证，
    由路由显式传入 ``PublicPrincipal`` 覆盖。
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise AuthError("缺少 Authorization: Bearer 头")
    token = auth_header[len("Bearer "):].strip()
    if not token:
        raise AuthError("Authorization 头为空")
    return await authenticator.authenticate(token)


# -- RBAC 依赖 ---------------------------------------------------------------


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    """仅 admin 可访问。"""
    return require_role("admin", principal)


def require_operator(principal: Principal = Depends(get_principal)) -> Principal:
    """admin / operator 可访问。"""
    return require_role("operator", principal)


def require_viewer(principal: Principal = Depends(get_principal)) -> Principal:
    """admin / operator / viewer 均可访问。"""
    return require_role("viewer", principal)


def get_service(request: Request, name: str) -> Any:
    """从 app.state 取服务单例（由 ``lifespan`` 装配）。"""
    service = getattr(request.app.state, name, None)
    if service is None:
        raise AuthError(f"服务 {name} 尚未装配", details={"service": name})
    return service
