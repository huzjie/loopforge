"""方言中立列类型 —— SQLite 与 PostgreSQL 16 双跑的关键。

四种类型（``TASK_BREAKDOWN`` §3.1.3「重中之重」）：

============== ================================ ==============================
类型            PostgreSQL                       SQLite
============== ================================ ==============================
:class:`JsonType`      ``JSONB``                        ``JSON``（TEXT 存储）
:class:`UtcDateTime`   ``TIMESTAMP WITH TIME ZONE``     ``DATETIME``（UTC naive）
:class:`StrEnumType`   ``VARCHAR(n)``                   ``VARCHAR(n)``
:class:`DecimalMoney`  ``NUMERIC(18,8)``                ``NUMERIC``（走字符串）
============== ================================ ==============================

.. warning::
   SQLite 不保存 tzinfo，:class:`UtcDateTime` 在写入时统一转成 UTC naive、
   读取时再补回 ``timezone.utc``。**任何绕过本类型直接用 ``DateTime`` 的列
   都会在 SQLite 上丢时区**，导致租约过期判断错误。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final, Generic, TypeVar

from sqlalchemy import Dialect, Numeric, String, Text
from sqlalchemy import JSON as SAJson
from sqlalchemy import DateTime as SADateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

from loopforge.constants import ID_COLUMN_LENGTH, MONEY_PRECISION, MONEY_SCALE

__all__ = [
    "JsonType",
    "UtcDateTime",
    "StrEnumType",
    "DecimalMoney",
    "IdColumnType",
    "ULIDType",
    "MONEY_ZERO",
]

E = TypeVar("E", bound=StrEnum)

#: 金额零值，避免各处重复构造。
MONEY_ZERO: Final[Decimal] = Decimal("0E-8")

#: ID 列类型别名（VARCHAR(26) 存 ULID）。
IdColumnType = String(ID_COLUMN_LENGTH)

#: 语义别名，可读性更好。
ULIDType = IdColumnType


class JsonType(TypeDecorator[Any]):
    """方言中立 JSON 列：PostgreSQL → ``JSONB``，其余 → ``JSON``。

    与原生 ``JSON`` 的区别：

    * ``None`` 与 SQL ``NULL`` 语义一致（不会写成 JSON 的 ``null`` 字符串）；
    * SQLite 上确保 ``ensure_ascii=False``，中文可读；
    * 明确标记 ``cache_ok = True``，允许 SQLAlchemy 复用编译缓存。
    """

    impl = SAJson
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        """按方言选择底层实现。

        Args:
            dialect: 当前方言。

        Returns:
            方言对应的类型实例。
        """
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB(astext_type=Text()))
        return dialect.type_descriptor(SAJson(none_as_null=True))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        """写入前处理。

        Args:
            value: Python 值。
            dialect: 当前方言。

        Returns:
            交给底层类型的值。
        """
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        """读取后处理。

        SQLite 在极少数历史数据里可能存了 JSON 字符串，这里做一次兼容解析。

        Args:
            value: 数据库返回值。
            dialect: 当前方言。

        Returns:
            Python 对象。
        """
        if isinstance(value, (str, bytes, bytearray)):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return value
        return value


class UtcDateTime(TypeDecorator[datetime]):
    """强制 UTC 的时间列。

    行为：

    * 写入：naive 视作 UTC；aware 转成 UTC；SQLite 上剥掉 tzinfo；
    * 读取：一律补回 ``timezone.utc``，返回 tz-aware :class:`datetime`。
    """

    impl = SADateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        """PostgreSQL 用 ``TIMESTAMPTZ``，SQLite 用普通 ``DATETIME``。

        Args:
            dialect: 当前方言。

        Returns:
            方言对应的类型实例。
        """
        if dialect.name == "postgresql":
            return dialect.type_descriptor(SADateTime(timezone=True))
        return dialect.type_descriptor(SADateTime(timezone=False))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """写入前统一成 UTC。

        Args:
            value: Python 时间值。
            dialect: 当前方言。

        Returns:
            规范化后的时间。

        Raises:
            TypeError: 传入的不是 :class:`datetime`。
        """
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"UtcDateTime 只接受 datetime，收到 {type(value).__name__}")
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        if dialect.name == "postgresql":
            return aware
        return aware.replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """读取后补回 UTC 时区。

        Args:
            value: 数据库返回值。
            dialect: 当前方言。

        Returns:
            tz-aware UTC 时间。
        """
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class StrEnumType(TypeDecorator[Any], Generic[E]):
    """把 :class:`enum.StrEnum` 以**字符串字面量**存库。

    为什么不用 SQLAlchemy 原生 ``Enum``：

    1. 原生 ``Enum`` 在 PostgreSQL 上会建 ``CREATE TYPE``，新增成员需要
       ``ALTER TYPE``，与「迁移不可变」原则冲突；
    2. 字符串存储让 DB 里的值肉眼可读，排障成本低；
    3. 跨库可移植，SQLite / PG 完全一致。

    非法值在读取时不会静默吞掉 —— 抛 :class:`ValueError`，宁可炸也不能返回脏枚举。
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[E], *, length: int = 32) -> None:
        """初始化。

        Args:
            enum_class: 目标 :class:`StrEnum` 子类。
            length: VARCHAR 长度，需覆盖最长成员名。

        Raises:
            TypeError: ``enum_class`` 不是 StrEnum 子类。
            ValueError: ``length`` 不足以容纳最长成员。
        """
        if not (isinstance(enum_class, type) and issubclass(enum_class, StrEnum)):
            raise TypeError(f"enum_class 必须是 StrEnum 子类，收到 {enum_class!r}")
        longest = max((len(member.value) for member in enum_class), default=0)
        if longest > length:
            raise ValueError(
                f"{enum_class.__name__} 最长成员 {longest} 字符，超过 length={length}"
            )
        self.enum_class = enum_class
        super().__init__(length=length)

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        """写入前转成字符串。

        Args:
            value: 枚举成员或其字符串值。
            dialect: 当前方言。

        Returns:
            字符串字面量。

        Raises:
            ValueError: 值不属于该枚举。
        """
        if value is None:
            return None
        if isinstance(value, self.enum_class):
            return str(value.value)
        member = self.enum_class(str(value))
        return str(member.value)

    def process_result_value(self, value: Any, dialect: Dialect) -> E | None:
        """读取后还原成枚举成员。

        Args:
            value: 数据库返回的字符串。
            dialect: 当前方言。

        Returns:
            枚举成员。

        Raises:
            ValueError: 数据库里存了非法值（数据损坏，必须暴露）。
        """
        if value is None:
            return None
        return self.enum_class(str(value))

    @property
    def python_type(self) -> type[E]:
        """对应的 Python 类型。"""
        return self.enum_class


class DecimalMoney(TypeDecorator[Decimal]):
    """金额列 ``NUMERIC(18, 8)``。

    SQLite 没有真正的定点数，SQLAlchemy 会用 float 中转并告警；
    这里显式走字符串绑定，保证「写进去多少读出来就是多少」。
    """

    impl = Numeric
    cache_ok = True

    def __init__(
        self, precision: int = MONEY_PRECISION, scale: int = MONEY_SCALE
    ) -> None:
        """初始化。

        Args:
            precision: 总位数。
            scale: 小数位数。
        """
        self.precision_value = precision
        self.scale_value = scale
        super().__init__(precision=precision, scale=scale, asdecimal=True)

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        """SQLite 上退化为字符串存储，避免 float 精度丢失。

        Args:
            dialect: 当前方言。

        Returns:
            方言对应的类型实例。
        """
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(self.precision_value + 3))
        return dialect.type_descriptor(
            Numeric(precision=self.precision_value, scale=self.scale_value, asdecimal=True)
        )

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        """写入前量化到固定小数位。

        Args:
            value: ``Decimal`` / ``int`` / ``float`` / 数字字符串。
            dialect: 当前方言。

        Returns:
            SQLite 返回字符串，其他方言返回 ``Decimal``。

        Raises:
            ValueError: 无法解析成十进制数。
        """
        if value is None:
            return None
        try:
            amount = value if isinstance(value, Decimal) else Decimal(str(value))
        except Exception as exc:  # noqa: BLE001 —— decimal 抛的是 InvalidOperation
            raise ValueError(f"无法把 {value!r} 解析为金额") from exc
        quantized = amount.quantize(Decimal(1).scaleb(-self.scale_value))
        if dialect.name == "sqlite":
            return format(quantized, "f")
        return quantized

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        """读取后还原成 :class:`Decimal`。

        Args:
            value: 数据库返回值。
            dialect: 当前方言。

        Returns:
            ``Decimal`` 值。
        """
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))
