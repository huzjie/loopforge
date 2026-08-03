#!/usr/bin/env python
"""校验 Alembic 迁移链只有单一 head —— pre-commit 钩子。

为什么需要它
------------

多分支并行开发时，两个人各自加一个 ``down_revision = '0006'`` 的迁移，
合并后就出现两个 head。此时 ``alembic upgrade head`` 会直接报错，
而错误发生在**部署现场**而不是 CI —— 这是最糟糕的失败时机。

本脚本不依赖 alembic 运行时（避免 pre-commit 环境要装全套依赖），
而是直接解析 ``alembic/versions/*.py`` 里的 ``revision`` 与 ``down_revision``
字面量，用图算法找出所有出度为 0 的节点。

同时校验：

* 无重复 revision id；
* 无悬空 down_revision（指向不存在的版本）；
* 无环；
* 有且只有一个 base（``down_revision = None``）；
* 有且只有一个 head。

用法::

    python scripts/check_alembic_heads.py [versions 目录]
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

#: 迁移目录相对项目根的默认位置。
DEFAULT_VERSIONS_DIR = Path("alembic") / "versions"


def _literal(node: ast.expr) -> str | None:
    """把 AST 字面量节点转成字符串或 ``None``。

    Args:
        node: 赋值语句右侧节点。

    Returns:
        字符串字面量；``None`` 字面量或无法识别时返回 ``None``。
    """
    if isinstance(node, ast.Constant):
        if node.value is None:
            return None
        if isinstance(node.value, str):
            return node.value
    return None


def parse_revision(path: Path) -> tuple[str | None, str | None]:
    """从迁移文件里解析出 ``(revision, down_revision)``。

    Args:
        path: 迁移文件路径。

    Returns:
        二元组；解析不到时对应位置为 ``None``。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None, None

    revision: str | None = None
    down_revision: str | None = None
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        if value is None:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "revision":
                revision = _literal(value)
            elif target.id == "down_revision":
                down_revision = _literal(value)
    return revision, down_revision


def collect_graph(versions_dir: Path) -> tuple[dict[str, str | None], list[str]]:
    """扫描目录构建迁移图。

    Args:
        versions_dir: ``alembic/versions`` 目录。

    Returns:
        ``(revision -> down_revision 映射, 错误列表)``。
    """
    graph: dict[str, str | None] = {}
    errors: list[str] = []
    for path in sorted(versions_dir.glob("*.py")):
        if path.name.startswith("__"):
            continue
        revision, down_revision = parse_revision(path)
        if revision is None:
            errors.append(f"{path.name}: 未找到 revision 字面量")
            continue
        if revision in graph:
            errors.append(f"{path.name}: revision '{revision}' 与其他迁移重复")
            continue
        graph[revision] = down_revision
    return graph, errors


def validate(graph: dict[str, str | None]) -> list[str]:
    """校验迁移图的结构完整性。

    Args:
        graph: ``revision -> down_revision`` 映射。

    Returns:
        问题描述列表，空列表表示通过。
    """
    problems: list[str] = []
    if not graph:
        return ["alembic/versions 下没有任何迁移文件"]

    # 悬空引用
    for revision, down in graph.items():
        if down is not None and down not in graph:
            problems.append(f"迁移 '{revision}' 的 down_revision '{down}' 不存在")

    # base 数量
    bases = [rev for rev, down in graph.items() if down is None]
    if len(bases) != 1:
        problems.append(f"应当有且只有一个 base（down_revision=None），实际 {len(bases)} 个：{bases}")

    # head：没有任何迁移把它当作 down_revision
    referenced = {down for down in graph.values() if down is not None}
    heads = sorted(rev for rev in graph if rev not in referenced)
    if len(heads) != 1:
        problems.append(
            f"应当有且只有一个 head，实际 {len(heads)} 个：{heads}；"
            "请用 `alembic merge` 合并分支，或修正 down_revision。"
        )

    # 环检测：从每个节点沿 down_revision 往回走
    for start in graph:
        seen: set[str] = set()
        cursor: str | None = start
        while cursor is not None:
            if cursor in seen:
                problems.append(f"迁移链中存在环，起点 '{start}'")
                break
            seen.add(cursor)
            cursor = graph.get(cursor)
    return sorted(set(problems))


def main(argv: list[str] | None = None) -> int:
    """入口。

    Args:
        argv: 参数列表；第一个参数可指定 versions 目录。

    Returns:
        进程返回码：0 通过，1 有问题。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    project_root = Path(__file__).resolve().parent.parent
    # pre-commit 会把改动的文件路径塞进来，这里只取目录型参数。
    dir_args = [Path(item) for item in args if Path(item).is_dir()]
    versions_dir = dir_args[0] if dir_args else project_root / DEFAULT_VERSIONS_DIR

    if not versions_dir.is_dir():
        print(f"迁移目录不存在：{versions_dir}", file=sys.stderr)
        return 1

    graph, errors = collect_graph(versions_dir)
    problems = errors + validate(graph)
    if problems:
        print("Alembic 迁移链校验失败：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    heads = [rev for rev in graph if rev not in {d for d in graph.values() if d is not None}]
    print(f"Alembic 迁移链正常：{len(graph)} 个版本，单一 head = {heads[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
