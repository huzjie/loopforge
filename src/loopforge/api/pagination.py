"""游标分页工具。

契约（§11.8）：``?cursor=<opaque>&limit=<1..200>``，**统一游标分页，不用 offset**。
游标编码为 base64(JSON)，内容为排序键的最后一个值；解码失败抛 ``LF-API-1507``。
"""

from __future__ import annotations

import base64
import json
from typing import Any

from loopforge.core.errors import ApiError

__all__ = ["encode_cursor", "decode_cursor", "DEFAULT_LIMIT", "MAX_LIMIT", "clamp_limit"]

DEFAULT_LIMIT: int = 50
MAX_LIMIT: int = 200


def clamp_limit(limit: int | None) -> int:
    """把 limit 夹到 ``[1, MAX_LIMIT]``。"""
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1:
        return 1
    if limit > MAX_LIMIT:
        return MAX_LIMIT
    return limit


def encode_cursor(value: dict[str, Any]) -> str:
    """把排序键编码为不透明游标。

    :param value: 例如 ``{"seq": 12345}`` 或 ``{"created_at": "..."}``。
    :returns: base64 URL 安全字符串。
    """
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str | None) -> dict[str, Any]:
    """解码游标；``None`` 表示第一页。

    :raises ApiError: 游标非法（``LF-API-1507``）。
    """
    if cursor is None or cursor == "":
        return {}
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("cursor 必须是 JSON 对象")
        return value
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(
            "游标无效或已过期", details={"cursor": cursor[:64]}
        ) from exc
