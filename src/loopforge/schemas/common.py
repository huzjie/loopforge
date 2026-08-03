"""公共 DTO 基座：基类、分页、错误响应、健康检查。

来源：``docs/ARCHITECTURE.md`` §7.1、``docs/TASK_BREAKDOWN.md`` §3.1.4。

.. note::
   **schemas 与 models 严格分离**。ORM 模型是持久化形态，DTO 是传输形态。
   两者字段可以重叠但生命周期不同 —— 加一个数据库列不该自动泄露到 API，
   加一个 API 字段也不该逼着改表。所有「ORM → DTO」的转换统一走
   ``XxxRead.model_validate(orm_obj)``（依赖 ``from_attributes=True``）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from loopforge.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from loopforge.core.enums import Severity

__all__ = [
    "LFModel",
    "ORMModel",
    "SortOrder",
    "PageMeta",
    "Page",
    "CursorPage",
    "PageQuery",
    "TimeRange",
    "ErrorDetail",
    "ErrorResponse",
    "OkResponse",
    "IdResponse",
    "HealthStatus",
    "HealthResponse",
    "VersionResponse",
    "T",
]

T = TypeVar("T")


class LFModel(BaseModel):
    """所有 DTO 的基类（纯输入 / 输出结构）。

    * ``extra="forbid"`` —— 请求体里出现未知字段直接 422，而不是被静默吞掉。
      长程自主运行最怕的就是「参数拼错了但系统装作没看见」。
    * ``str_strip_whitespace`` —— 统一去掉首尾空白，避免 ``"  main "``
      这种分支名混进 Git 命令。
    * ``use_enum_values=False`` —— 保留 StrEnum 实例，让类型检查继续有效。
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
        populate_by_name=True,
        ser_json_timedelta="float",
    )


class ORMModel(LFModel):
    """从 ORM 实例构造的 DTO 基类。

    读模型统一继承它，这样 ``Read.model_validate(orm_row)`` 才能工作。
    ``extra="ignore"`` 是刻意的：ORM 行上会有一堆 DTO 不关心的属性。
    """

    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",
        str_strip_whitespace=True,
        use_enum_values=False,
        populate_by_name=True,
    )


class SortOrder(StrEnum):
    """排序方向。"""

    ASC = "asc"
    DESC = "desc"


class PageMeta(LFModel):
    """分页元信息。"""

    total: int = Field(default=0, ge=0, description="满足条件的记录总数")
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)

    @property
    def has_more(self) -> bool:
        """是否还有下一页。"""
        return self.offset + self.limit < self.total

    @property
    def page_index(self) -> int:
        """当前页序号（从 0 开始）。"""
        return self.offset // self.limit if self.limit > 0 else 0


class Page(LFModel, Generic[T]):
    """偏移分页响应体。"""

    items: list[T] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)

    @classmethod
    def of(
        cls,
        items: list[T],
        *,
        total: int | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> Page[T]:
        """便捷构造。

        :param items: 当前页数据。
        :param total: 总数；``None`` 时退化为 ``offset + len(items)``。
        :param limit: 页大小。
        :param offset: 偏移。
        """
        resolved_total = total if total is not None else offset + len(items)
        return cls(
            items=items,
            meta=PageMeta(total=resolved_total, limit=limit, offset=offset),
        )


class CursorPage(LFModel, Generic[T]):
    """游标分页响应体 —— 事件流这类高频追加的数据用它。

    偏移分页在 append-only 表上会「跳记录」：翻页期间新数据插进来，
    ``OFFSET 100`` 指向的行就变了。游标分页锚定 ``next_cursor``（通常是
    ``events.seq``），天然免疫。
    """

    items: list[T] = Field(default_factory=list)
    next_cursor: str | None = Field(default=None, description="下一页游标；None 表示到底")
    has_more: bool = Field(default=False)


class PageQuery(LFModel):
    """列表接口的通用查询参数。"""

    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)
    order: SortOrder = Field(default=SortOrder.DESC)

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, value: int) -> int:
        """夹紧页大小（双保险，防止绕过 Field 约束的内部调用）。"""
        return max(1, min(value, MAX_PAGE_SIZE))


class TimeRange(LFModel):
    """闭开区间 ``[start, end)`` 的时间过滤条件。"""

    start: datetime | None = Field(default=None)
    end: datetime | None = Field(default=None)

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        """校验 ``start <= end``。

        :raises ValueError: 区间倒挂。
        """
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("时间区间倒挂：start 必须早于或等于 end")
        return self

    @property
    def is_empty(self) -> bool:
        """是否未设置任何边界。"""
        return self.start is None and self.end is None


class ErrorDetail(LFModel):
    """错误响应的详情块。"""

    code: str = Field(description="LF-<DOMAIN>-<NNNN> 形式的稳定错误码")
    message: str = Field(description="面向人的错误描述")
    details: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = Field(default=None)
    retryable: bool = Field(default=False, description="调用方是否可以直接重试")


class ErrorResponse(LFModel):
    """统一错误响应体 —— 所有 4xx / 5xx 都长这样。"""

    error: ErrorDetail

    @classmethod
    def of(
        cls,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        trace_id: str | None = None,
        retryable: bool = False,
    ) -> ErrorResponse:
        """便捷构造。"""
        return cls(
            error=ErrorDetail(
                code=code,
                message=message,
                details=details or {},
                trace_id=trace_id,
                retryable=retryable,
            )
        )


class OkResponse(LFModel):
    """无返回值动作的确认响应。"""

    ok: bool = Field(default=True)
    message: str = Field(default="")


class IdResponse(LFModel):
    """创建类接口的最小响应。"""

    id: str = Field(description="新建资源的 ULID")


class HealthStatus(StrEnum):
    """健康检查状态。"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class HealthResponse(LFModel):
    """``GET /healthz`` 的响应体。"""

    status: HealthStatus = Field(default=HealthStatus.HEALTHY)
    version: str = Field(default="")
    checks: dict[str, bool] = Field(
        default_factory=dict, description="子系统 → 是否健康，如 {'db': True}"
    )
    severity: Severity = Field(default=Severity.INFO)
    checked_at: datetime | None = Field(default=None)

    @model_validator(mode="after")
    def _derive_status(self) -> Self:
        """根据 ``checks`` 自动推导总状态与严重级。

        全部通过 → healthy；部分失败 → degraded；全部失败 → unhealthy。
        """
        if not self.checks:
            return self
        failed = [name for name, ok in self.checks.items() if not ok]
        if not failed:
            self.status = HealthStatus.HEALTHY
            self.severity = Severity.INFO
        elif len(failed) == len(self.checks):
            self.status = HealthStatus.UNHEALTHY
            self.severity = Severity.CRITICAL
        else:
            self.status = HealthStatus.DEGRADED
            self.severity = Severity.WARNING
        return self


class VersionResponse(LFModel):
    """``GET /version`` 的响应体。"""

    name: str = Field(default="loopforge")
    version: str = Field(default="")
    api_version: str = Field(default="v1")
    schema_revision: str = Field(default="", description="当前 alembic head")
    python: str = Field(default="")
