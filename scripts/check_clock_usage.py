#!/usr/bin/env python
"""禁止直呼系统时钟 —— pre-commit 钩子。

为什么这条规则值得一个专门的钩子
--------------------------------

LoopForge 要跑 16 天无人干预，时间相关的 bug（租约过期、预算窗口翻滚、
超时审批）几乎无法靠人工复现。唯一可行的办法是让**所有**时间读取都经过
:class:`loopforge.core.clock.Clock`，测试里换成 ``FrozenClock`` 就能把
「三天后租约过期」压缩成一次断言。

只要有一处偷偷调了 ``datetime.now()``，这套确定性就整体失效 —— 所以这里
用 AST 扫描而不是正则：正则会被 ``import datetime as dt`` 之类的写法绕过去。

被禁调用
--------

* ``datetime.now()`` / ``datetime.utcnow()`` / ``datetime.today()``
* ``time.time()`` / ``time.monotonic()`` / ``time.perf_counter()``
* ``date.today()``

豁免
----

* ``src/loopforge/core/clock.py`` 本身（时钟的实现总得读系统时间）；
* ``tests/`` 下的文件（测试可以自己造时间）；
* ``alembic/`` 下的迁移（``create_date`` 注释无关运行时）；
* 显式标注 ``# noqa: clock`` 的行。

用法::

    python scripts/check_clock_usage.py [文件...]

不传文件时扫描 ``src/`` 全量。返回码 1 表示发现违规。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: 被禁止的「模块.属性」调用组合。
FORBIDDEN_CALLS: dict[str, frozenset[str]] = {
    "datetime": frozenset({"now", "utcnow", "today"}),
    "date": frozenset({"today"}),
    "time": frozenset({"time", "monotonic", "perf_counter", "time_ns", "monotonic_ns"}),
}

#: 豁免路径片段（用 posix 风格比较，Windows 上同样有效）。
EXEMPT_PARTS: tuple[str, ...] = (
    "loopforge/core/clock.py",
    "/tests/",
    "tests/",
    "/alembic/",
    "alembic/",
    "scripts/check_clock_usage.py",
)

#: 行内豁免标记。
INLINE_EXEMPT = "# noqa: clock"


class ClockVisitor(ast.NodeVisitor):
    """收集违规调用的 AST 访问器。"""

    def __init__(self, path: Path, source_lines: list[str]) -> None:
        """初始化。

        Args:
            path: 被检查文件路径。
            source_lines: 源码行列表，用于读取行内豁免标记。
        """
        self.path = path
        self.source_lines = source_lines
        self.violations: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 —— ast 访问器命名约定
        """检查每一处函数调用。

        Args:
            node: 调用节点。
        """
        target = self._describe(node.func)
        if target is not None:
            self.violations.append((node.lineno, target))
        self.generic_visit(node)

    def _describe(self, func: ast.expr) -> str | None:
        """判断调用是否命中黑名单。

        Args:
            func: 被调用的表达式。

        Returns:
            命中时返回可读的调用描述，否则 ``None``。
        """
        if not isinstance(func, ast.Attribute):
            return None
        attr = func.attr
        owner = func.value

        # 形如 datetime.now() / time.time()
        if isinstance(owner, ast.Name):
            allowed = FORBIDDEN_CALLS.get(owner.id)
            if allowed and attr in allowed and not self._is_exempt_line(func.lineno):
                return f"{owner.id}.{attr}()"
            return None

        # 形如 datetime.datetime.now()
        if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name):
            allowed = FORBIDDEN_CALLS.get(owner.attr)
            if allowed and attr in allowed and not self._is_exempt_line(func.lineno):
                return f"{owner.value.id}.{owner.attr}.{attr}()"
        return None

    def _is_exempt_line(self, lineno: int) -> bool:
        """该行是否带了行内豁免标记。

        Args:
            lineno: 1 起始的行号。

        Returns:
            是否豁免。
        """
        if 1 <= lineno <= len(self.source_lines):
            return INLINE_EXEMPT in self.source_lines[lineno - 1]
        return False


def is_exempt_path(path: Path) -> bool:
    """判断文件是否整体豁免。

    Args:
        path: 文件路径。

    Returns:
        是否豁免。
    """
    posix = path.as_posix()
    return any(part in posix for part in EXEMPT_PARTS)


def check_file(path: Path) -> list[str]:
    """检查单个文件。

    Args:
        path: 文件路径。

    Returns:
        违规描述列表，空列表表示通过。
    """
    if is_exempt_path(path):
        return []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{path}: 无法读取（{exc}）"]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: 语法错误，无法检查（{exc.msg}）"]

    visitor = ClockVisitor(path, source.splitlines())
    visitor.visit(tree)
    return [
        f"{path}:{lineno}: 禁止直接调用 {call}，请改用 loopforge.core.clock.get_clock()"
        for lineno, call in visitor.violations
    ]


def collect_targets(args: list[str]) -> list[Path]:
    """解析命令行参数得到待检查文件列表。

    Args:
        args: 命令行参数（文件路径）。

    Returns:
        待检查的 ``.py`` 文件列表。
    """
    if args:
        return [Path(item) for item in args if item.endswith(".py")]
    root = Path(__file__).resolve().parent.parent / "src"
    return sorted(root.rglob("*.py"))


def main(argv: list[str] | None = None) -> int:
    """入口。

    Args:
        argv: 参数列表，默认取 ``sys.argv[1:]``。

    Returns:
        进程返回码：0 通过，1 发现违规。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    problems: list[str] = []
    for path in collect_targets(args):
        problems.extend(check_file(path))

    if problems:
        print("发现违反「时钟必须可注入」铁律的调用：", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\n修复方式：注入 Clock 依赖，或在函数签名里加 `now: datetime | None = None`。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
