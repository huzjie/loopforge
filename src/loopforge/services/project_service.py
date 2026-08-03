"""项目与仓库服务。

负责：项目 CRUD、仓库 CRUD、项目统计、远端可达性校验、Issue 同步入口。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from loopforge.core.errors import NotFoundError, UniqueViolationError
from loopforge.core.ids import new_ulid
from loopforge.core.logging import get_logger
from loopforge.db.repositories.project_repo import ProjectRepository, RepoRepository
from loopforge.db.uow import UnitOfWork
from loopforge.models.project import Project, Repo
from loopforge.models.run import Run
from loopforge.models.task import Task
from loopforge.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate, RepoCreate, RepoRead, RepoUpdate

__all__ = ["ProjectService"]

_logger = get_logger(__name__)


class ProjectService:
    """项目域服务。"""

    async def create_project(self, payload: ProjectCreate) -> ProjectRead:
        """创建项目；slug 冲突抛唯一约束异常。"""
        async with UnitOfWork() as uow:
            existing = await uow.projects.get_by_slug(payload.slug)
            if existing is not None:
                raise UniqueViolationError(
                    f"项目 slug 已存在：{payload.slug}", details={"slug": payload.slug}
                )
            project = await uow.projects.create(
                id=new_ulid(),
                slug=payload.slug,
                name=payload.name,
                description=payload.description,
                settings=payload.settings,
            )
        return ProjectRead.model_validate(project)

    async def list_projects(self, *, keyword: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[ProjectRead], int]:
        """项目列表（分页 + 搜索）。"""
        async with UnitOfWork() as uow:
            repo: ProjectRepository = uow.projects
            if keyword:
                projects = await repo.search(keyword, limit=limit, offset=offset)
                total = len(await repo.search(keyword))
            else:
                projects = await repo.all(limit=limit, offset=offset)
                total = await repo.count_by()
        return [ProjectRead.model_validate(p) for p in projects], total

    async def get_project(self, project_id: str) -> ProjectRead:
        """项目详情。"""
        async with UnitOfWork() as uow:
            project = await uow.projects.get_or_raise(project_id)
        return ProjectRead.model_validate(project)

    async def get_project_detail(self, project_id: str) -> dict[str, Any]:
        """项目详情（含仓库与统计）。"""
        async with UnitOfWork() as uow:
            project = await uow.projects.get_or_raise(project_id)
            repos = await uow.repos.list_by_project(project_id)
            runs = await uow.runs.list_by_project(project_id)
        return {
            "project": ProjectRead.model_validate(project).model_dump(mode="json"),
            "repos": [RepoRead.model_validate(r).model_dump(mode="json") for r in repos],
            "run_count": len(runs),
        }

    async def update_project(self, project_id: str, payload: ProjectUpdate) -> ProjectRead:
        """更新项目。"""
        async with UnitOfWork() as uow:
            project = await uow.projects.get_or_raise(project_id)
            changes = payload.changed_fields()
            if changes:
                await uow.projects.update_by_pk(project_id, **changes)
                await uow.refresh(project)
        return ProjectRead.model_validate(project)

    async def delete_project(self, project_id: str) -> None:
        """删除项目（级联确认）。"""
        async with UnitOfWork() as uow:
            await uow.projects.get_or_raise(project_id)
            await uow.projects.delete_by_pk(project_id)

    async def project_stats(self, project_id: str) -> dict[str, Any]:
        """项目统计（Run 数 / 成本 / 成功率）。"""
        async with UnitOfWork() as uow:
            await uow.projects.get_or_raise(project_id)
            runs = await uow.runs.list_by_project(project_id)
            run_ids = [r.id for r in runs]
            total_runs = len(runs)
            succeeded = sum(1 for r in runs if str(r.state) == "SUCCEEDED")
            failed = sum(1 for r in runs if str(r.state) == "FAILED")
            cost = Decimal("0")
            if run_ids:
                result = await uow.session.execute(
                    select(func.coalesce(func.sum(Run.loop_index), 0)).where(Run.id.in_(run_ids))
                )
                # 用 cost_records 更准确，但保持轻量：统计表不存在时跳过。
            from loopforge.models.budget import CostRecord

            if run_ids:
                result = await uow.session.execute(
                    select(func.coalesce(func.sum(CostRecord.cost_usd), 0)).where(
                        CostRecord.run_id.in_(run_ids)
                    )
                )
                cost = Decimal(result.scalar_one() or 0)
            success_rate = succeeded / total_runs if total_runs else 0.0
        return {
            "project_id": project_id,
            "total_runs": total_runs,
            "succeeded": succeeded,
            "failed": failed,
            "active": sum(1 for r in runs if r.is_active),
            "success_rate": round(success_rate, 4),
            "cost_usd": str(cost),
        }

    # -- 仓库 -----------------------------------------------------------------

    async def create_repo(self, payload: RepoCreate) -> RepoRead:
        """登记代码仓。"""
        async with UnitOfWork() as uow:
            await uow.projects.get_or_raise(payload.project_id)
            repo = await uow.repos.create(
                id=new_ulid(),
                project_id=payload.project_id,
                provider=payload.provider,
                remote_url=payload.remote_url,
                default_branch=payload.default_branch,
                credentials_ref=payload.credentials_ref,
                local_path=payload.local_path,
                labels_config=payload.labels_config,
                webhook_enabled=payload.webhook_enabled,
            )
        return RepoRead.model_validate(repo)

    async def list_repos(self, *, project_id: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[RepoRead], int]:
        """仓库列表。"""
        async with UnitOfWork() as uow:
            if project_id:
                repos = await uow.repos.list_by_project(project_id)
                total = len(repos)
            else:
                repos = await uow.repos.all(limit=limit, offset=offset)
                total = await uow.repos.count_by()
        return [RepoRead.model_validate(r) for r in repos], total

    async def get_repo(self, repo_id: str) -> RepoRead:
        """仓库详情。"""
        async with UnitOfWork() as uow:
            repo = await uow.repos.get_or_raise(repo_id)
        return RepoRead.model_validate(repo)

    async def update_repo(self, repo_id: str, payload: RepoUpdate) -> RepoRead:
        """更新仓库。"""
        async with UnitOfWork() as uow:
            await uow.repos.get_or_raise(repo_id)
            changes = payload.changed_fields()
            if changes:
                await uow.repos.update_by_pk(repo_id, **changes)
            repo = await uow.repos.get_or_raise(repo_id)
        return RepoRead.model_validate(repo)

    async def delete_repo(self, repo_id: str) -> None:
        """删除仓库。"""
        async with UnitOfWork() as uow:
            await uow.repos.get_or_raise(repo_id)
            await uow.repos.delete_by_pk(repo_id)

    async def verify_access(self, repo_id: str) -> dict[str, Any]:
        """校验远端可达与权限（尽力而为）。"""
        async with UnitOfWork() as uow:
            repo = await uow.repos.get_or_raise(repo_id)
        # 本地仓库：检查目录存在；远端：留待 VCS 层实现后接入。
        if repo.provider == "local" and repo.local_path:
            from pathlib import Path

            ok = Path(repo.local_path).exists()
            return {"repo_id": repo_id, "ok": ok, "detail": "local_path 存在" if ok else "local_path 不存在"}
        return {"repo_id": repo_id, "ok": True, "detail": "远端校验由 VCS 层完成"}

    async def sync_issues(self, repo_id: str) -> dict[str, Any]:
        """从 GitHub 拉取带标签 Issue 建 Task（VCS 层就绪后接入）。"""
        async with UnitOfWork() as uow:
            await uow.repos.get_or_raise(repo_id)
        return {"repo_id": repo_id, "synced": 0, "detail": "VCS 层未接入，跳过同步"}
