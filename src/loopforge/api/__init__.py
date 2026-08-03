"""LoopForge HTTP API 层（WP7）。

对外唯一事实来源：141 个 REST 端点 + 1 个 WS 通道（``docs/TASK_BREAKDOWN.md`` §9.1.1）。

使用方式::

    from loopforge.api import create_app
    app = create_app()
    # uvicorn loopforge.api.app:app
"""

from __future__ import annotations

from loopforge.api.app import create_app

__all__ = ["create_app"]
