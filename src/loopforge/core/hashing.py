"""稳定序列化与哈希 —— 全仓**唯一**实现（``TASK_BREAKDOWN`` §11.2 冻结）。

.. warning::
   禁止任何包自行 ``json.dumps`` 后再哈希。幂等键、审计哈希链、内容寻址产物
   三处都依赖「同一输入 → 同一字节」，一旦有第二套序列化实现，崩溃恢复的
   幂等探测就会误判。

冻结签名::

    def canonical_json(obj: Any) -> str: ...
    def sha256_hex(data: str | bytes) -> str: ...
    def chain_hash(prev_hash: str | None, payload: Mapping[str, Any]) -> str: ...
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Mapping, Sequence, Set
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Any, Final
from uuid import UUID

__all__ = [
    "canonical_json",
    "sha256_hex",
    "chain_hash",
    "content_hash",
    "short_hash",
    "hmac_sha256_hex",
    "verify_hmac",
    "idempotency_key",
    "simhash64",
    "hamming_distance",
    "GENESIS_HASH",
    "HASH_HEX_LENGTH",
]

#: 审计哈希链的创世哈希 —— ``prev_hash is None`` 时使用。
GENESIS_HASH: Final[str] = "0" * 64

#: sha256 十六进制摘要长度。
HASH_HEX_LENGTH: Final[int] = 64

#: canonical_json 的分隔符：无空格，保证字节级稳定。
_SEPARATORS: Final[tuple[str, str]] = (",", ":")

#: simhash 分词用的粗粒度切分正则（中英混排友好）。
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _default(obj: Any) -> Any:
    """``json.dumps`` 的兜底编码器 —— 把常见非 JSON 原生类型转成稳定表示。

    Args:
        obj: 任意不被 ``json`` 直接支持的对象。

    Returns:
        可被 ``json`` 序列化的等价值。

    Raises:
        TypeError: 对象无法确定性序列化时抛出（调用方应显式转换）。
    """
    if isinstance(obj, Enum):
        # StrEnum / IntEnum 都走 value，保证与 DB 存储一致。
        return obj.value
    if isinstance(obj, Decimal):
        # 用字符串而非 float，避免二进制浮点抖动破坏哈希稳定性。
        return format(obj.normalize(), "f")
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, PurePath):
        # 统一成 POSIX 分隔符，Windows 与 Linux 上哈希一致。
        return obj.as_posix()
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return bytes(obj).hex()
    if isinstance(obj, Set):
        # 集合无序 → 先转成可比较的字符串再排序，确保稳定。
        return sorted((canonical_json(item) for item in obj))
    if isinstance(obj, Mapping):
        return dict(obj)
    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes)):
        return list(obj)
    if isinstance(obj, Iterable):
        return list(obj)
    if hasattr(obj, "model_dump"):
        # Pydantic v2 模型。
        dumped = obj.model_dump(mode="json")
        return dumped
    raise TypeError(f"canonical_json 无法确定性序列化类型 {type(obj).__name__!r}")


def canonical_json(obj: Any) -> str:
    """把任意对象序列化成**字节稳定**的 JSON 字符串。

    规则（三者缺一不可，改动即破坏全仓幂等）：

    1. ``sort_keys=True`` —— 字典键升序，消除插入顺序影响；
    2. ``separators=(",", ":")`` —— 无多余空格；
    3. ``ensure_ascii=False`` —— 中文原样输出，不转 ``\\uXXXX``。

    Args:
        obj: 待序列化对象。支持 dict / list / 标量 / Enum / Decimal /
            datetime / UUID / Path / bytes / set / Pydantic 模型。

    Returns:
        规范化 JSON 字符串。

    Raises:
        TypeError: 存在无法确定性序列化的类型。

    Examples:
        >>> canonical_json({"b": 1, "a": "中文"})
        '{"a":"中文","b":1}'
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=_SEPARATORS,
        ensure_ascii=False,
        default=_default,
        allow_nan=False,
    )


def sha256_hex(data: str | bytes) -> str:
    """计算 SHA-256 十六进制摘要。

    Args:
        data: 字符串（按 UTF-8 编码）或字节串。

    Returns:
        64 个小写十六进制字符。
    """
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    return hashlib.sha256(raw).hexdigest()


def chain_hash(prev_hash: str | None, payload: Mapping[str, Any]) -> str:
    """计算审计哈希链的下一环。

    公式::

        hash_n = sha256( (prev_hash or GENESIS_HASH) + "|" + canonical_json(payload) )

    Args:
        prev_hash: 上一条记录的哈希；链首传 ``None``。
        payload: 本条记录的内容。

    Returns:
        64 位十六进制哈希。

    Examples:
        >>> h1 = chain_hash(None, {"type": "RUN_CREATED"})
        >>> h2 = chain_hash(h1, {"type": "RUN_STARTED"})
        >>> h1 != h2
        True
    """
    anchor = prev_hash if prev_hash else GENESIS_HASH
    return sha256_hex(f"{anchor}|{canonical_json(payload)}")


def content_hash(obj: Any) -> str:
    """对任意对象做内容寻址哈希（产物去重、Checkpoint 比对用）。

    Args:
        obj: 任意可 :func:`canonical_json` 化的对象。

    Returns:
        64 位十六进制哈希。
    """
    return sha256_hex(canonical_json(obj))


def short_hash(data: str | bytes, length: int = 12) -> str:
    """取哈希前缀，用于日志与分支名等对长度敏感的场景。

    Args:
        data: 输入数据。
        length: 需要的前缀长度，取值 ``1..64``。

    Returns:
        指定长度的十六进制前缀。

    Raises:
        ValueError: ``length`` 越界。
    """
    if not 1 <= length <= HASH_HEX_LENGTH:
        raise ValueError(f"length 必须在 1..{HASH_HEX_LENGTH} 之间，收到 {length}")
    return sha256_hex(data)[:length]


def hmac_sha256_hex(key: str | bytes, message: str | bytes) -> str:
    """计算 HMAC-SHA256（GitHub Webhook 签名校验用）。

    Args:
        key: 共享密钥。
        message: 待签名报文。

    Returns:
        64 位十六进制签名。
    """
    key_bytes = key.encode("utf-8") if isinstance(key, str) else bytes(key)
    msg_bytes = message.encode("utf-8") if isinstance(message, str) else bytes(message)
    return hmac.new(key_bytes, msg_bytes, hashlib.sha256).hexdigest()


def verify_hmac(key: str | bytes, message: str | bytes, signature: str) -> bool:
    """恒定时间比较 HMAC 签名。

    Args:
        key: 共享密钥。
        message: 原始报文。
        signature: 待校验签名，允许带 ``sha256=`` 前缀。

    Returns:
        校验通过返回 ``True``。
    """
    expected = hmac_sha256_hex(key, message)
    actual = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
    return hmac.compare_digest(expected, actual.strip().lower())


def idempotency_key(
    *,
    run_id: str,
    task_id: str,
    loop_index: int,
    phase: str,
    action: str,
    args: Mapping[str, Any] | None = None,
) -> str:
    """生成 Step 幂等键 —— 崩溃恢复「相同意图只执行一次」的唯一凭据。

    键的语义：**同一个 Run 的同一个 Task 的同一轮的同一阶段的同一动作与参数，
    无论重启多少次，键都完全相同**。

    Args:
        run_id: Run ULID。
        task_id: Task ULID。
        loop_index: 循环序号，从 0 开始。
        phase: 循环阶段（plan/act/observe/verify/commit）。
        action: 动作名（通常是工具名或角色名）。
        args: 动作参数，参与哈希；``None`` 视作空字典。

    Returns:
        形如 ``idem_<40 位十六进制>`` 的幂等键，可直接写入
        ``steps.idempotency_key`` 唯一索引列。

    Raises:
        ValueError: ``loop_index`` 为负数。
    """
    if loop_index < 0:
        raise ValueError(f"loop_index 不能为负，收到 {loop_index}")
    digest = content_hash(
        {
            "run_id": run_id,
            "task_id": task_id,
            "loop_index": loop_index,
            "phase": phase,
            "action": action,
            "args": dict(args or {}),
        }
    )
    return f"idem_{digest[:40]}"


def simhash64(text: str) -> int:
    """计算 64 位 SimHash（演化工作项去重用）。

    Args:
        text: 输入文本，中英混排均可。

    Returns:
        64 位无符号整数指纹；空文本返回 ``0``。
    """
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return 0
    vector = [0] * 64
    for token in tokens:
        # 取 sha256 前 8 字节作为 token 指纹。
        fingerprint = int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:8], "big")
        for bit in range(64):
            if fingerprint >> bit & 1:
                vector[bit] += 1
            else:
                vector[bit] -= 1
    result = 0
    for bit in range(64):
        if vector[bit] > 0:
            result |= 1 << bit
    return result


def hamming_distance(left: int, right: int) -> int:
    """两个整数指纹的汉明距离。

    Args:
        left: 指纹 A。
        right: 指纹 B。

    Returns:
        不同比特位的个数。
    """
    return int((left ^ right).bit_count())
