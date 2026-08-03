"""通用状态机 —— 声明式转移表 + 守卫 + 副作用钩子。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1 / ``docs/ARCHITECTURE.md`` §6。

设计要点
--------
1. **声明式转移表**：``StateMachine[StateT]`` 接收 ``states`` 与 ``transitions``，
   非法转移在构造期即可静态校验（``from_state`` / ``to_state`` 必须属于状态集）。
2. **守卫**：每条转移可挂 ``guard``（同步或异步谓词），不满足则转移被拒。
3. **副作用钩子**：``on_transition`` 在转移成功后执行（事件发射、计数递增等）。
4. **并发安全**：单实例状态机只服务于一个 Worker 的领域对象，不做跨进程锁；
   跨进程的一致性由 DB 条件 UPDATE（CAS）保证，状态机只负责「合法转移判定」。

非法转移统一抛 :class:`~loopforge.core.errors.IllegalTransitionError`
（``LF-RUN-0302`` / ``LF-TASK-0403``），调用方按实体区分。
"""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from loopforge.core.errors import IllegalTransitionError

__all__ = [
    "StateT",
    "TransitionRule",
    "TransitionResult",
    "GuardFn",
    "StateMachine",
]

StateT = TypeVar("StateT")

#: 守卫谓词：接收 (from_state, to_state, context) 返回是否放行。
#: 同步与异步均可。
GuardFn = Callable[[StateT, StateT, dict[str, Any] | None], bool | Awaitable[bool]]

#: 转移副作用钩子：接收 (from_state, to_state, context)。
HookFn = Callable[[StateT, StateT, dict[str, Any] | None], Any | Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class TransitionRule(Generic[StateT]):
    """一条合法转移的声明。

    Attributes:
        from_state: 源状态。
        to_state: 目标状态。
        name: 转移名（人类可读，日志与事件 reason 用）。
        guard: 可选守卫；不满足时转移被拒（不算非法，只是被守卫拦下）。
        on_transition: 可选副作用钩子（事件发射 / 计数 / 指标）。
    """

    from_state: StateT
    to_state: StateT
    name: str = ""
    guard: GuardFn | None = None
    on_transition: HookFn | None = None

    def __post_init__(self) -> None:
        # frozen dataclass 需要 object.__setattr__ 覆盖默认值。
        object.__setattr__(self, "name", self.name or f"{self.from_state}->{self.to_state}")


@dataclass(slots=True)
class TransitionResult(Generic[StateT]):
    """一次成功转移的结果。"""

    from_state: StateT
    to_state: StateT
    rule: TransitionRule[StateT]
    context: dict[str, Any] | None = field(default_factory=dict)


class StateMachine(Generic[StateT]):
    """声明式状态机。

    Example:
        >>> sm = StateMachine(
        ...     states=["A", "B"],
        ...     transitions=[TransitionRule("A", "B", name="go")],
        ...     initial="A",
        ... )
        >>> sm.current
        'A'
        >>> asyncio.run(sm.transition("A", "B"))
    """

    def __init__(
        self,
        *,
        states: Sequence[StateT],
        transitions: Sequence[TransitionRule[StateT]],
        initial: StateT,
        entity: str = "entity",
    ) -> None:
        """初始化状态机。

        :param states: 全部合法状态。
        :param transitions: 合法转移表。
        :param initial: 初始状态。
        :param entity: 实体名（``run`` / ``task`` / ``step`` / ``lease``），
            出现在异常 details 里便于定位。
        :raises IllegalTransitionError: 转移表引用未注册状态 / 初始状态非法。
        """
        if initial not in states:
            raise IllegalTransitionError(
                f"初始状态 {initial!r} 不属于状态集",
                details={"entity": entity, "states": [str(s) for s in states]},
            )
        self._states: tuple[StateT, ...] = tuple(states)
        self._entity: str = entity
        self._current: StateT = initial
        self._table: dict[tuple[StateT, StateT], TransitionRule[StateT]] = {}
        for rule in transitions:
            if rule.from_state not in self._states or rule.to_state not in self._states:
                raise IllegalTransitionError(
                    f"转移 {rule.from_state}->{rule.to_state} 引用了未注册状态",
                    details={
                        "entity": entity,
                        "from": str(rule.from_state),
                        "to": str(rule.to_state),
                    },
                )
            key = (rule.from_state, rule.to_state)
            if key in self._table:
                raise IllegalTransitionError(
                    f"转移 {key} 重复声明",
                    details={"entity": entity, "existing": self._table[key].name},
                )
            self._table[key] = rule
        self._history: list[TransitionResult[StateT]] = []

    # -- 查询 ---------------------------------------------------------------

    @property
    def current(self) -> StateT:
        """当前状态。"""
        return self._current

    @property
    def states(self) -> tuple[StateT, ...]:
        """全部合法状态。"""
        return self._states

    @property
    def history(self) -> list[TransitionResult[StateT]]:
        """已发生的转移历史（测试与审计用）。"""
        return list(self._history)

    def transitions_from(self, state: StateT) -> list[TransitionRule[StateT]]:
        """列出从 ``state`` 出发的全部合法转移。"""
        return [
            rule
            for (src, _dst), rule in self._table.items()
            if src == state
        ]

    def can_transition(
        self, from_state: StateT, to_state: StateT, *, context: dict[str, Any] | None = None
    ) -> tuple[bool, str]:
        """判断 (from, to) 是否合法（不执行）。

        :returns: ``(ok, reason)``；``ok=False`` 时 ``reason`` 说明原因。
        """
        if from_state == to_state:
            return True, "self_transition_allowed"
        rule = self._table.get((from_state, to_state))
        if rule is None:
            return False, (
                f"非法转移：{from_state} -> {to_state}（实体 {self._entity}）"
            )
        return True, rule.name

    def assert_legal(
        self, from_state: StateT, to_state: StateT, *, context: dict[str, Any] | None = None
    ) -> None:
        """非法转移直接抛 :class:`IllegalTransitionError`。"""
        ok, reason = self.can_transition(from_state, to_state, context=context)
        if not ok:
            raise IllegalTransitionError(
                reason,
                details={
                    "entity": self._entity,
                    "from": str(from_state),
                    "to": str(to_state),
                },
            )

    # -- 转移 ---------------------------------------------------------------

    async def transition(
        self,
        from_state: StateT,
        to_state: StateT,
        *,
        context: dict[str, Any] | None = None,
        force: bool = False,
    ) -> TransitionResult[StateT]:
        """执行一次状态转移。

        :param from_state: 期望的当前状态（与实例当前态不一致时抛错）。
        :param to_state: 目标状态。
        :param context: 传给守卫与副作用钩子的上下文。
        :param force: ``True`` 时跳过守卫（仅限系统级强制转移，默认关闭）。
        :returns: 转移结果。
        :raises IllegalTransitionError: 非法转移、当前态不匹配、或守卫拒绝。
        """
        if self._current != from_state:
            raise IllegalTransitionError(
                f"当前状态 {self._current} 与期望 {from_state} 不一致",
                details={"entity": self._entity, "expected": str(from_state)},
            )
        self.assert_legal(from_state, to_state, context=context)
        rule = self._table[(from_state, to_state)]
        if not force and rule.guard is not None:
            verdict = rule.guard(from_state, to_state, context)
            if isinstance(verdict, Awaitable):
                verdict = await verdict
            if not verdict:
                raise IllegalTransitionError(
                    f"守卫拒绝转移 {rule.name}",
                    details={
                        "entity": self._entity,
                        "from": str(from_state),
                        "to": str(to_state),
                        "rule": rule.name,
                    },
                )
        result = TransitionResult(
            from_state=from_state, to_state=to_state, rule=rule, context=dict(context or {})
        )
        self._current = to_state
        self._history.append(result)
        if rule.on_transition is not None:
            effect = rule.on_transition(from_state, to_state, context)
            if isinstance(effect, Awaitable):
                await effect
        return result

    async def jump(self, state: StateT, *, context: dict[str, Any] | None = None) -> None:
        """直接跳到某个状态（不经过转移表）—— 仅用于加载持久化状态。

        :raises IllegalTransitionError: 目标不在状态集。
        """
        if state not in self._states:
            raise IllegalTransitionError(
                f"状态 {state!r} 不属于状态集",
                details={"entity": self._entity},
            )
        self._current = state


class StateTable(Generic[StateT]):
    """纯函数式状态转移表 —— 供状态机与 DB CAS 双路校验共用。

    与 :class:`StateMachine` 的区别：本类不持有「当前状态」，只提供
    「(from, to) 是否合法」的查表能力，方便在仓储层做 CAS 前的二次校验。
    """

    def __init__(
        self,
        *,
        states: Sequence[StateT],
        transitions: Sequence[tuple[StateT, StateT]],
        entity: str = "entity",
    ) -> None:
        self._states: tuple[StateT, ...] = tuple(states)
        self._entity: str = entity
        self._allowed: set[tuple[StateT, StateT]] = set()
        for src, dst in transitions:
            if src not in self._states or dst not in self._states:
                raise IllegalTransitionError(
                    f"转移 {src}->{dst} 引用了未注册状态",
                    details={"entity": entity},
                )
            self._allowed.add((src, dst))

    def is_legal(self, from_state: StateT, to_state: StateT) -> bool:
        """判断 (from, to) 是否合法。"""
        if from_state == to_state:
            return True
        return (from_state, to_state) in self._allowed

    def legal_targets(self, from_state: StateT) -> list[StateT]:
        """列出从 ``from_state`` 出发的全部合法目标。"""
        return [dst for (src, dst) in sorted(self._allowed, key=str) if src == from_state]
