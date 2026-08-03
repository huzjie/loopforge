"""进展检测器 —— 动作指纹环形缓冲 + 文件变更净值 + 无变更计数。

来源：``docs/TASK_BREAKDOWN.md`` §6.1.1 / §6.4。

两种信号
--------
* ``NO_PROGRESS_DETECTED``：连续 ``no_progress_threshold`` 轮既无动作指纹变化
  也无文件变更净值 —— Run 可能在原地打转；
* ``ACTION_LOOP_DETECTED``：动作指纹环形缓冲里同一动作重复出现超过阈值 ——
  LLM 反复调用同一工具同一参数（死循环）。

本检测器**只发信号不决策**，由 LoopEngine / Monitor 消费。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from loopforge.core.hashing import canonical_json, sha256_hex

__all__ = ["ProgressSignal", "ProgressDetector"]


@dataclass(slots=True)
class ProgressSignal:
    """进展信号。"""

    kind: str  # NO_PROGRESS_DETECTED | ACTION_LOOP_DETECTED
    consecutive_loops: int = 0
    threshold: int = 0
    signature: str = ""
    window: int = 0
    repeated_action: str = ""
    occurrences: int = 0

    def to_dict(self) -> dict[str, Any]:
        """转字典（事件 payload 用）。"""
        return {
            "kind": self.kind,
            "consecutive_loops": self.consecutive_loops,
            "threshold": self.threshold,
            "signature": self.signature,
            "window": self.window,
            "repeated_action": self.repeated_action,
            "occurrences": self.occurrences,
        }


class ProgressDetector:
    """动作指纹环形缓冲 + 文件变更净值。

    :param no_progress_threshold: 连续无进展多少轮触发 NO_PROGRESS。
    :param action_loop_window: 环形缓冲窗口大小。
    :param action_loop_repeats: 同一动作出现多少次判定为 ACTION_LOOP。
    """

    def __init__(
        self,
        *,
        no_progress_threshold: int = 5,
        action_loop_window: int = 8,
        action_loop_repeats: int = 4,
    ) -> None:
        self._no_progress_threshold: int = max(1, no_progress_threshold)
        self._window: int = max(2, action_loop_window)
        self._repeats: int = max(2, action_loop_repeats)
        self._actions: deque[str] = deque(maxlen=self._window)
        self._consecutive_no_progress: int = 0
        self._last_signature: str = ""
        self._file_change_net: int = 0
        self._seen_paths: set[str] = set()

    # -- 记录 ---------------------------------------------------------------

    def record_action(self, tool_name: str, args: dict[str, Any] | None = None) -> None:
        """记录一次工具动作（归一化参数哈希入缓冲）。"""
        fingerprint = sha256_hex(
            canonical_json({"tool": tool_name, "args": args or {}})
        )[:24]
        self._actions.append(fingerprint)

    def record_file_change(self, path: str, *, delta: int = 1) -> None:
        """记录一次文件变更（净值增减）。"""
        if path not in self._seen_paths:
            self._seen_paths.add(path)
            self._file_change_net += delta

    def record_round(self) -> None:
        """每轮结束时调用：根据动作与文件变化更新无进展计数。"""
        signature = self._round_signature()
        if signature and signature == self._last_signature and self._file_change_net == 0:
            self._consecutive_no_progress += 1
        else:
            self._consecutive_no_progress = 0
        self._last_signature = signature
        self._file_change_net = 0
        self._seen_paths.clear()

    # -- 检测 ---------------------------------------------------------------

    def detect(self) -> ProgressSignal | None:
        """返回当前应上报的信号；无信号返回 ``None``。"""
        # 动作死循环优先。
        counts: dict[str, int] = {}
        for fp in self._actions:
            counts[fp] = counts.get(fp, 0) + 1
        repeated = max(counts.items(), key=lambda kv: kv[1], default=("", 0))
        if repeated[1] >= self._repeats:
            return ProgressSignal(
                kind="ACTION_LOOP_DETECTED",
                window=self._window,
                repeated_action=repeated[0],
                occurrences=repeated[1],
            )
        if self._consecutive_no_progress >= self._no_progress_threshold:
            return ProgressSignal(
                kind="NO_PROGRESS_DETECTED",
                consecutive_loops=self._consecutive_no_progress,
                threshold=self._no_progress_threshold,
                signature=self._last_signature,
            )
        return None

    # -- 状态 ---------------------------------------------------------------

    def reset(self) -> None:
        """清空全部状态（新 Task / 新 Run 起点）。"""
        self._actions.clear()
        self._consecutive_no_progress = 0
        self._last_signature = ""
        self._file_change_net = 0
        self._seen_paths.clear()

    def _round_signature(self) -> str:
        """本轮动作序列的指纹（空则返回空串）。"""
        if not self._actions:
            return ""
        return sha256_hex(canonical_json(list(self._actions)))[:24]
