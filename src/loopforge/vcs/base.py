"""``BaseVcsProvider`` —— 版本控制提供者统一契约（§11.10 冻结）。

来源：``docs/TASK_BREAKDOWN.md`` §11.10。

冻结签名（一个字符不能改）::

    class BaseVcsProvider(abc.ABC):
        kind: ClassVar[str]                              # "local_sandbox" | "github"

        async def clone(self, *, repo: RepoRef, dest: str, branch: str | None = None) -> CloneResult: ...
        async def create_branch(self, *, repo: RepoRef, name: str, base: str) -> BranchRef: ...
        async def commit(self, *, repo: RepoRef, message: str, paths: Sequence[str]) -> CommitRef: ...
        async def push(self, *, repo: RepoRef, branch: str, force: bool = False) -> PushResult: ...
        async def create_pr(self, *, repo: RepoRef, head: str, base: str,
                            title: str, body: str) -> PullRequestRef: ...
        async def update_pr(self, *, repo: RepoRef, number: int, **fields: Any) -> PullRequestRef: ...
        async def comment(self, *, repo: RepoRef, number: int, body: str) -> CommentRef: ...
        async def list_issues(self, *, repo: RepoRef, labels: Sequence[str] = ()) -> list[IssueRef]: ...
        async def sync_issue(self, *, repo: RepoRef, task: Task) -> IssueRef: ...
        async def get_checks(self, *, repo: RepoRef, sha: str) -> list[CheckRunRef]: ...

约定
----
* ``local_sandbox`` 与 ``github`` 必须通过同一套契约测试（§8.3-5），
  保证零 Key 模式与真实模式行为一致。
* ``repo`` 用 :class:`RepoRef` 描述（轻量值对象，不依赖 ORM 行），
  携带 ``repo_id`` 供 DB 侧关联（PR 存 artifacts 等）。
* ``Task`` 参数用 ``Any`` 标注：避免 vcs 包反向依赖 ORM，映射由
  :mod:`loopforge.vcs.github.mapper` 负责。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Sequence

__all__ = [
    "RepoRef",
    "CloneResult",
    "BranchRef",
    "CommitRef",
    "PushResult",
    "PullRequestRef",
    "IssueRef",
    "CheckRunRef",
    "CommentRef",
    "BaseVcsProvider",
]


@dataclass(frozen=True)
class RepoRef:
    """一次 VCS 操作的仓库定位（值对象）。

    Attributes:
        remote_url: 远端 URL（本地沙箱为 bare 仓库路径，GitHub 为 https URL）。
        provider: ``local_sandbox`` / ``github``。
        default_branch: 默认分支（如 ``main``）。
        local_path: 本地工作区路径（``None`` 表示尚未 clone）。
        credentials_ref: 凭据引用（provider_configs 名 / env 名，不存明文）。
        repo_id: ``repos`` 表主键（PR 落库关联用，可为 ``None``）。
        meta: 扩展元数据（run_id / task_id 等，PR 落库用）。
    """

    remote_url: str = ""
    provider: str = "local_sandbox"
    default_branch: str = "main"
    local_path: str | None = None
    credentials_ref: str | None = None
    repo_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        """仓库目录名（从 URL 派生，用于沙箱目录隔离）。"""
        url = self.remote_url.strip().rstrip("/")
        name = url.rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[: -len(".git")]
        return name or "repo"


@dataclass(frozen=True)
class CloneResult:
    """clone 结果。"""

    path: str
    branch: str
    commit_sha: str = ""
    provider_kind: str = ""


@dataclass(frozen=True)
class BranchRef:
    """分支引用。"""

    name: str
    sha: str = ""
    base: str = ""


@dataclass(frozen=True)
class CommitRef:
    """一次提交引用。"""

    sha: str
    short_sha: str = ""
    message: str = ""
    author: str = ""

    def __post_init__(self) -> None:
        """自动补短 sha。"""
        if not self.short_sha and self.sha:
            object.__setattr__(self, "short_sha", self.sha[:12])


@dataclass(frozen=True)
class PushResult:
    """push 结果。"""

    ok: bool = True
    remote: str = "origin"
    branch: str = ""
    commits_pushed: int = 0
    forced: bool = False
    error: str = ""


@dataclass(frozen=True)
class PullRequestRef:
    """PR 引用。"""

    number: int
    url: str = ""
    title: str = ""
    head: str = ""
    base: str = ""
    state: str = "open"
    body: str = ""
    mergeable: bool | None = None
    merged: bool = False

    @property
    def is_merged(self) -> bool:
        """是否已合并。"""
        return self.merged or self.state == "merged"


@dataclass(frozen=True)
class IssueRef:
    """Issue 引用。"""

    number: int
    title: str = ""
    body: str = ""
    state: str = "open"
    labels: tuple[str, ...] = ()
    url: str = ""


@dataclass(frozen=True)
class CheckRunRef:
    """CI Check Run 引用。"""

    name: str
    status: str = "queued"
    conclusion: str = ""
    url: str = ""
    sha: str = ""

    @property
    def succeeded(self) -> bool:
        """是否成功结束。"""
        return self.status == "completed" and self.conclusion == "success"


@dataclass(frozen=True)
class CommentRef:
    """评论引用。"""

    id: str = ""
    url: str = ""
    body: str = ""
    created_at: datetime | None = None


class BaseVcsProvider(abc.ABC):
    """VCS 提供者抽象基类（§11.10 冻结）。"""

    kind: ClassVar[str] = ""

    @abc.abstractmethod
    async def clone(self, *, repo: RepoRef, dest: str, branch: str | None = None) -> CloneResult: ...

    @abc.abstractmethod
    async def create_branch(self, *, repo: RepoRef, name: str, base: str) -> BranchRef: ...

    @abc.abstractmethod
    async def commit(self, *, repo: RepoRef, message: str, paths: Sequence[str]) -> CommitRef: ...

    @abc.abstractmethod
    async def push(self, *, repo: RepoRef, branch: str, force: bool = False) -> PushResult: ...

    @abc.abstractmethod
    async def create_pr(self, *, repo: RepoRef, head: str, base: str, title: str, body: str) -> PullRequestRef: ...

    @abc.abstractmethod
    async def update_pr(self, *, repo: RepoRef, number: int, **fields: Any) -> PullRequestRef: ...

    @abc.abstractmethod
    async def comment(self, *, repo: RepoRef, number: int, body: str) -> CommentRef: ...

    @abc.abstractmethod
    async def list_issues(self, *, repo: RepoRef, labels: Sequence[str] = ()) -> list[IssueRef]: ...

    @abc.abstractmethod
    async def sync_issue(self, *, repo: RepoRef, task: Any) -> IssueRef: ...

    @abc.abstractmethod
    async def get_checks(self, *, repo: RepoRef, sha: str) -> list[CheckRunRef]: ...
