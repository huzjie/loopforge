"""失败路由器 —— 把验证结果转成结构化修复建议（§5.3.6）。

输出不是一段 stderr 文本，而是 :class:`FailureReport`：包含失败分类、
受影响的阶段、修复提示与定位线索 —— 这是 WP4 生成修复子任务的直接输入。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from loopforge.verification.base import FailureClass, VerificationResult, VerificationStage
from loopforge.verification.detectors import extract_failure_hints

__all__ = ["FailureReport", "FailureRouter"]

#: 失败分类 → 修复提示模板。
_FIX_HINTS: dict[FailureClass, str] = {
    FailureClass.COMPILE: "先修复编译/语法错误（看 hints 中的第一个 error 行），再重新验证",
    FailureClass.ASSERT: "测试断言失败：要么修实现，要么修测试期望，先定位首个失败用例",
    FailureClass.TIMEOUT: "阶段超时：检查是否有死循环/挂起进程，缩短单测范围或增加超时",
    FailureClass.ENV: "环境问题：检查依赖安装、权限、工具链是否可用，再重试",
    FailureClass.DEPENDENCY: "依赖问题：核对版本约束与 lockfile，缺失依赖先安装再重试",
    FailureClass.UNKNOWN: "无法自动归类：查看 stdout_tail 与 hints 人工判断",
}


class FailureReport(BaseModel):
    """一次失败的完整报告。"""

    model_config = ConfigDict(extra="forbid")

    stage: VerificationStage
    failure_class: FailureClass
    ok: bool = False
    summary: str = ""
    hints: list[str] = Field(default_factory=list)
    fix_hint: str = ""
    exit_code: int = 0
    details: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """导出结构化字典。"""
        return {
            "stage": self.stage.value,
            "failure_class": self.failure_class.value,
            "ok": self.ok,
            "summary": self.summary,
            "hints": self.hints,
            "fix_hint": self.fix_hint,
            "exit_code": self.exit_code,
            "details": self.details,
        }


class FailureRouter:
    """把 :class:`VerificationResult` 路由成 :class:`FailureReport`。"""

    def route(self, result: VerificationResult) -> FailureReport:
        """路由单个结果。

        Args:
            result: 验证结果。

        Returns:
            :class:`FailureReport`；通过的结果也返回（``ok=True``）。
        """
        hints = extract_failure_hints(stdout=result.stdout_tail, stderr="")
        fix_hint = _FIX_HINTS.get(result.failure_class, _FIX_HINTS[FailureClass.UNKNOWN])
        if result.ok:
            fix_hint = "无需修复"
        return FailureReport(
            stage=result.stage,
            failure_class=result.failure_class,
            ok=result.ok,
            summary=result.summary,
            hints=hints,
            fix_hint=fix_hint,
            exit_code=int(result.details.get("exit_code", 0)),
            details=result.details,
        )

    def route_many(self, results: list[VerificationResult]) -> list[FailureReport]:
        """批量路由，保持顺序。"""
        return [self.route(item) for item in results]

    def first_blocking(self, results: list[VerificationResult]) -> FailureReport | None:
        """返回第一个阻塞性的失败（按阶段顺序）。

        Args:
            results: 按执行顺序排列的结果。

        Returns:
            第一个 ``ok=False`` 的报告；全部通过返回 ``None``。
        """
        for item in results:
            if not item.ok:
                return self.route(item)
        return None
