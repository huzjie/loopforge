"""验证闭环 —— 数据结构与校验器抽象基类。

对应 Qwen3.8 的 Self-testing 范式：**Build → Unit Test → E2E → 生命周期校验**。
每个阶段产出结构化 :class:`VerificationResult`（失败分类不是一段 stderr 文本，
而是 :class:`FailureClass` 枚举 —— §5.3.6 要求，WP4 据此生成修复子任务）。
"""

from __future__ import annotations

import abc
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "VerificationStage",
    "VerificationStatus",
    "FailureClass",
    "VerificationResult",
    "Verifier",
]


class VerificationStage(StrEnum):
    """验证阶段。"""

    BUILD = "BUILD"
    UNIT_TEST = "UNIT_TEST"
    E2E = "E2E"
    LINT = "LINT"
    LIFECYCLE = "LIFECYCLE"


class VerificationStatus(StrEnum):
    """验证状态。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    TIMEOUT = "TIMEOUT"


class FailureClass(StrEnum):
    """失败分类（WP4 修复子任务的输入）。"""

    COMPILE = "COMPILE"          # 编译 / 构建失败
    ASSERT = "ASSERT"            # 断言失败（测试逻辑问题）
    TIMEOUT = "TIMEOUT"          # 超时
    ENV = "ENV"                  # 环境问题（缺依赖、权限、工具缺失）
    DEPENDENCY = "DEPENDENCY"    # 依赖版本 / 解析问题
    UNKNOWN = "UNKNOWN"          # 无法归类的失败


class VerificationResult(BaseModel):
    """单个阶段的验证结果。"""

    model_config = ConfigDict(extra="forbid")

    stage: VerificationStage
    status: VerificationStatus = VerificationStatus.PENDING
    ok: bool = False
    duration_ms: int = 0
    summary: str = ""
    stdout_tail: str = Field(default="", max_length=8000)
    failure_class: FailureClass = FailureClass.UNKNOWN
    details: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)

    @classmethod
    def skipped(cls, stage: VerificationStage, reason: str) -> VerificationResult:
        """构造跳过结果。"""
        return cls(
            stage=stage,
            status=VerificationStatus.SKIPPED,
            ok=True,
            summary=reason,
            failure_class=FailureClass.UNKNOWN,
        )

    def to_dict(self) -> dict[str, Any]:
        """导出结构化字典（供 report / LLM 消费）。"""
        return {
            "stage": self.stage.value,
            "status": self.status.value,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "failure_class": self.failure_class.value,
            "details": self.details,
            "artifacts": self.artifacts,
        }


class Verifier(abc.ABC):
    """校验器抽象基类。

    子类必须声明 :attr:`stage` 并实现 :meth:`verify`。
    """

    #: 本校验器负责的阶段。
    stage: ClassVar[VerificationStage] = VerificationStage.BUILD

    @abc.abstractmethod
    async def verify(self, executor: Any, workspace: Any, *, timeout_s: float = 600.0) -> VerificationResult:
        """执行校验。

        Args:
            executor: 执行器（BaseExecutor）。
            workspace: 工作区句柄。
            timeout_s: 阶段超时秒数。

        Returns:
            :class:`VerificationResult`。**任何情况下不抛异常**，失败转成
            ``status=FAILED`` 的结果。
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{type(self).__name__}(stage={self.stage.value})"


# ---------------------------------------------------------------------------
# 共享执行辅助
# ---------------------------------------------------------------------------


def make_spec(executor: Any, workspace: Any, argv: list[str], cwd: str, timeout_s: float) -> Any:
    """构造 :class:`~loopforge.executors.base.CommandSpec`。"""
    from loopforge.executors.base import CommandSpec

    return CommandSpec(handle_id=workspace.id, argv=argv, cwd=cwd, timeout_s=timeout_s)


def summarize_output(result: Any, *, limit: int = 4000) -> str:
    """把命令结果压成摘要。"""
    return result.combined_output(limit=limit)
