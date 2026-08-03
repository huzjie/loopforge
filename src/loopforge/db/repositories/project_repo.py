"""Project / Repo 仓储。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from loopforge.db.repositories.base import BaseRepository, clamp_limit
from loopforge.models.project import Project, Repo

__all__ = ["ProjectRepository", "RepoRepository"]


class ProjectRepository(BaseRepository[Project]):
    """项目持久化。"""

    model = Project

    async def get_by_slug(self, slug: str) -> Project | None:
        """按 slug 取项目（``uq_projects_slug``）。"""
        result = await self.session.execute(select(Project).where(Project.slug == slug))
        return result.scalar_one_or_none()

    async def get_with_repos(self, project_id: str) -> Project | None:
        """取项目并一次性加载其代码仓（避免 N+1）。"""
        result = await self.session.execute(
            select(Project)
            .options(selectinload(Project.repos))
            .where(Project.id == project_id)
        )
        return result.scalar_one_or_none()

    async def search(
        self, keyword: str, *, limit: int | None = None, offset: int = 0
    ) -> list[Project]:
        """按名称或 slug 模糊搜索。

        :param keyword: 关键字；空串等价于列出全部。
        """
        stmt = select(Project)
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(Project.name.like(pattern) | Project.slug.like(pattern))
        stmt = (
            stmt.order_by(Project.created_at.desc())
            .offset(max(0, offset))
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class RepoRepository(BaseRepository[Repo]):
    """代码仓配置持久化。"""

    model = Repo

    async def list_by_project(
        self, project_id: str, *, limit: int | None = None
    ) -> list[Repo]:
        """列出某项目下的全部代码仓。"""
        stmt = (
            select(Repo)
            .where(Repo.project_id == project_id)
            .order_by(Repo.created_at)
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_remote(self, project_id: str, remote_url: str) -> Repo | None:
        """按 ``(project_id, remote_url)`` 取代码仓（``uq_repos_project_remote``）。"""
        result = await self.session.execute(
            select(Repo).where(Repo.project_id == project_id, Repo.remote_url == remote_url)
        )
        return result.scalar_one_or_none()

    async def list_webhook_enabled(self, *, limit: int | None = None) -> list[Repo]:
        """列出启用了 Webhook 的代码仓（WP6 注册回调时用）。"""
        stmt = (
            select(Repo)
            .where(Repo.webhook_enabled.is_(True))
            .order_by(Repo.created_at)
            .limit(clamp_limit(limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
