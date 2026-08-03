"""``python -m loopforge`` 入口。

CLI 本体由 **WP7** 交付（``loopforge/cli/main.py`` 的 ``app``）。
WP1 阶段先做惰性 import 兜底：CLI 还没落地时给出可读提示而不是 ImportError 堆栈，
这样 ``python -m loopforge`` 在 W0 就能跑通，不阻塞 G1 闸门。
"""

from __future__ import annotations

import sys
from typing import Any, NoReturn

from loopforge import __version__

#: CLI 未就绪时的退出码。
EXIT_CLI_UNAVAILABLE: int = 69  # EX_UNAVAILABLE

#: 用法提示。
_USAGE = """\
LoopForge {version}

用法：
  python -m loopforge <command> [options]

当前状态：
  CLI 子系统由 WP7 交付，尚未安装到本环境。

W0（内核底座）阶段可用的入口：
  python -c "import loopforge; print(loopforge.__version__)"
  python -c "from loopforge.core import get_settings; print(get_settings().db.url)"
  alembic upgrade head          # 建全部数据表
  pytest tests/unit             # 跑内核与数据层单测
"""


def _load_cli_app() -> Any | None:
    """惰性加载 WP7 的 Typer app。

    Returns:
        ``loopforge.cli.main.app``；模块尚未交付时返回 ``None``。
    """
    try:
        from loopforge.cli.main import app  # noqa: PLC0415 —— 必须惰性导入
    except ModuleNotFoundError:
        return None
    return app


def main(argv: list[str] | None = None) -> int:
    """程序入口。

    Args:
        argv: 命令行参数（不含程序名）；``None`` 时取 :data:`sys.argv`。

    Returns:
        进程退出码。``0`` 表示成功。
    """
    args = list(sys.argv[1:] if argv is None else argv)

    if args and args[0] in {"-V", "--version"}:
        sys.stdout.write(f"loopforge {__version__}\n")
        return 0

    app = _load_cli_app()
    if app is None:
        stream = sys.stdout if args and args[0] in {"-h", "--help"} else sys.stderr
        stream.write(_USAGE.format(version=__version__))
        return 0 if args and args[0] in {"-h", "--help"} else EXIT_CLI_UNAVAILABLE

    app(args=args, prog_name="loopforge", standalone_mode=False)
    return 0


def _entrypoint() -> NoReturn:
    """``python -m loopforge`` 的实际执行体。

    Raises:
        SystemExit: 总是以 :func:`main` 的返回值退出。
    """
    raise SystemExit(main())


if __name__ == "__main__":
    _entrypoint()
