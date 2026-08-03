"""``RoleRouter`` —— 按阶段 + 任务类型 + 上轮结果选择角色（§11.9 冻结签名）。

冻结签名::

    class RoleRouter:
        def select(self, *, phase: str, task_kind: str,
                   last_output: RoleOutput | None = None) -> RoleName: ...

设计要点
--------
1. **阶段优先**：``plan`` → Planner；``review`` → Reviewer；``summarize`` →
   Summarizer；``test/verify`` → Tester；其余（实现类阶段）按 ``task_kind``
   在 Coder / Tester 之间选择。
2. **上轮结果接管**：``last_output.handoff_to`` 非空时优先采用（Coder 自查完
   → Tester；Reviewer 未通过 → Coder；Tester 失败 → Coder）。
3. **Reviewer 越权被拒（G3 闸门 #6）**：Reviewer 只能被选到 review / summarize
   阶段；任何「实现 / 修复 / 测试」阶段请求 Reviewer 都会抛
   :class:`RoleError`（``LF-ROLE-1604``）。这是编排层对「Reviewer 只能 review
   不能 implement」的第二道闸（第一道是 ``allowed_tools`` 硬边界）。
4. **配置覆盖 + 单角色降级**：``overrides`` 按阶段覆盖；``single_role`` 强制
   全流程单角色（测试与降级场景）。
"""

from __future__ import annotations

from typing import Any, ClassVar, Final

from loopforge.core.enums import RoleName
from loopforge.roles.base import RoleOutput, RoleUnavailableError

__all__ = ["RoleRouter", "PHASE_ROLE_DEFAULT", "IMPLEMENT_PHASES"]

#: 阶段 → 默认角色（阶段名小写）。
PHASE_ROLE_DEFAULT: Final[dict[str, RoleName]] = {
    "plan": RoleName.PLANNER,
    "planning": RoleName.PLANNER,
    "review": RoleName.REVIEWER,
    "reviewing": RoleName.REVIEWER,
    "summarize": RoleName.SUMMARIZER,
    "summarizing": RoleName.SUMMARIZER,
    "summary": RoleName.SUMMARIZER,
    "test": RoleName.TESTER,
    "testing": RoleName.TESTER,
    "verify": RoleName.TESTER,
    "verifying": RoleName.TESTER,
}

#: 属于「实现」类阶段 —— Reviewer 一律不可进入。
IMPLEMENT_PHASES: Final[frozenset[str]] = frozenset(
    {"implement", "implementing", "execute", "executing", "act", "coding", "code", "fix", "fixing"}
)

#: 任务类型 → 默认实现角色。
TASK_KIND_ROLE: Final[dict[str, RoleName]] = {
    "test": RoleName.TESTER,
    "testing": RoleName.TESTER,
    "test-writing": RoleName.TESTER,
    "verification": RoleName.TESTER,
    "bugfix-test": RoleName.TESTER,
    "fix": RoleName.CODER,
    "code": RoleName.CODER,
    "refactor": RoleName.CODER,
    "docs": RoleName.CODER,
    "other": RoleName.CODER,
}


class RoleRouter:
    """角色路由器。"""

    def __init__(
        self,
        *,
        overrides: dict[str, RoleName] | None = None,
        single_role: RoleName | None = None,
    ) -> None:
        """初始化路由器。

        Args:
            overrides: 阶段名（小写）→ 角色的覆盖映射。
            single_role: 非空时全流程强制使用该角色（单角色降级模式）。
        """
        self._overrides: dict[str, RoleName] = dict(overrides or {})
        self._single_role: RoleName | None = single_role

    # ------------------------------------------------------------------ 主入口

    def select(
        self,
        *,
        phase: str,
        task_kind: str,
        last_output: RoleOutput | None = None,
    ) -> RoleName:
        """选择下一个执行角色。

        Args:
            phase: Loop 阶段（``plan`` / ``implement`` / ``review`` /
                ``test`` / ``summarize``，大小写不敏感）。
            task_kind: 任务类型（``code`` / ``test`` / ``fix`` / ...）。
            last_output: 上一轮角色输出（可为 ``None``）。

        Returns:
            选中的 :class:`RoleName`。

        Raises:
            RoleError: Reviewer 被请求进入实现阶段（``LF-ROLE-1604``）。
        """
        if self._single_role is not None:
            self._guard_reviewer_phase(self._single_role, phase)
            return self._single_role

        phase_norm = (phase or "").strip().lower()
        if phase_norm in self._overrides:
            selected = self._overrides[phase_norm]
            self._guard_reviewer_phase(selected, phase)
            return selected

        # 1) 上轮输出的 handoff_to 优先接管
        if last_output is not None and last_output.handoff_to is not None:
            selected = last_output.handoff_to
            self._guard_reviewer_phase(selected, phase)
            return selected

        # 2) 阶段直接映射
        if phase_norm in PHASE_ROLE_DEFAULT:
            selected = PHASE_ROLE_DEFAULT[phase_norm]
            self._guard_reviewer_phase(selected, phase)
            return selected

        # 3) 实现阶段：按任务类型选择 Coder / Tester
        if phase_norm in IMPLEMENT_PHASES or not phase_norm:
            kind = (task_kind or "code").strip().lower()
            return TASK_KIND_ROLE.get(kind, RoleName.CODER)

        # 4) 未知阶段：保守选择 Coder（实现语义）
        return RoleName.CODER

    # ------------------------------------------------------------------ 守卫

    @staticmethod
    def _guard_reviewer_phase(role: RoleName, phase: str) -> None:
        """Reviewer 只能出现在 review / summarize 阶段。"""
        phase_norm = (phase or "").strip().lower()
        if role == RoleName.REVIEWER and phase_norm in IMPLEMENT_PHASES:
            raise RoleUnavailableError(
                f"Reviewer 只能 review，不能进入实现阶段 {phase!r}（G3 闸门 #6）",
                details={"role": role.value, "phase": phase},
            )
