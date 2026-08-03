"""``BaseTool`` 抽象基类与工具层数据契约（**签名已冻结**）。

冻结来源：``docs/TASK_BREAKDOWN.md`` §11.5 / ``docs/ARCHITECTURE.md`` §7.3。

契约要点
--------
1. 每个工具必须声明 :attr:`side_effect_class`（四选一）—— 它直接决定崩溃恢复时
   WP4 是重放还是重规划。**漏声明 = 恢复逻辑失效**，本模块用元类兜底：
   具体子类未覆写 ``side_effect_class`` 直接报错。
2. ``PROBEABLE`` / ``IDEMPOTENT`` 工具必须实现 :meth:`probe`，且探测证据可验证
   （git trailer / 文件 sha256 / PR 查询 / Approval 记录）。
3. :meth:`execute` 返回 :class:`ToolResult`，**不抛裸异常** —— 一切失败都转成
   ``ok=False + error_code``，便于编排层统一处理。
4. 参数 Schema 用 Pydantic 模型表达（:attr:`args_schema`），
   :meth:`to_spec` 导出给 LLM function calling。

说明
----
* :class:`ToolContext` 里的 ``vcs`` / ``policy`` 字段在 WP5（governance）与
  VCS 包交付前用 ``Any | None`` 占位，字段名与顺序保持冻结契约不变。
* :class:`StepIntent` 在 WP4（orchestration）交付前由本模块提供，字段是
  ``probe()`` 的全部输入 —— WP4 直接复用，无需等待。
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from loopforge.core.enums import SideEffectClass
from loopforge.core.errors import ToolError, ToolNotRegisteredError

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from loopforge.providers.types import ToolSpec

try:  # pragma: no cover - providers 包（WP2）可能尚未就绪
    from loopforge.providers.types import ToolSpec
except Exception:  # noqa: BLE001 - WP2 WIP 时回退到结构一致的本地产物
    class ToolSpec(BaseModel):
        """工具声明（providers.types.ToolSpec 的结构等价回退）。"""

        model_config = ConfigDict(extra="forbid")

        name: str
        description: str = ""
        parameters: dict[str, Any] = Field(
            default_factory=lambda: {"type": "object", "properties": {}}
        )

__all__ = [
    "ToolError",
    "ToolNotRegisteredError",
    "ToolContext",
    "ToolResult",
    "ToolPreview",
    "ProbeResult",
    "StepIntent",
    "BaseTool",
]


# ---------------------------------------------------------------------------
# 数据契约
# ---------------------------------------------------------------------------


class _ToolModel(BaseModel):
    """工具层 DTO 基类。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ToolContext(_ToolModel):
    """工具执行上下文（**字段冻结**，ARCHITECTURE §7.3）。

    Attributes:
        run_id: Run ID。
        task_id: Task ID。
        step_id: Step（幂等单元）ID。
        trace_id: 链路 ID。
        workspace: 工作区句柄。
        executor: 执行器（工具用它执行命令 / 文件操作）。
        vcs: VCS 提供者（WP 交付前为 ``None``）。
        policy: 策略引擎（WP5 交付前为 ``None``，工具层回退到执行层策略）。
        idempotency_key: 本步幂等键（probe / trailer 用）。
        fence_token: 租约 fencing token。
    """

    run_id: str = Field(min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=64)
    step_id: str = Field(min_length=1, max_length=64)
    trace_id: str = Field(default="", max_length=64)
    workspace: Any | None = None
    executor: Any | None = None
    vcs: Any | None = None
    policy: Any | None = None
    idempotency_key: str = Field(default="", max_length=128)
    fence_token: int = 0


class ToolResult(_ToolModel):
    """工具执行结果（**字段冻结**，ARCHITECTURE §7.3）。

    Attributes:
        ok: 是否成功。
        output: 给 LLM 看的文本输出（**必须截断**，上限见
            ``loopforge.constants.MAX_TOOL_OUTPUT_BYTES``）。
        data: 结构化数据（文件 hash / 测试统计等）。
        external_ref: 外部世界引用（commit sha / PR 号 / 文件 hash）。
        error_code: 失败时的错误码（``LF-TOOL-*`` 等）。
        truncated: 输出是否被截断。
        bytes_out: 实际输出字节数。
    """

    ok: bool = True
    output: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    external_ref: str | None = None
    error_code: str | None = None
    truncated: bool = False
    bytes_out: int = Field(default=0, ge=0)

    @classmethod
    def failure(cls, message: str, *, error_code: str = "LF-TOOL-0902", **data: Any) -> ToolResult:
        """构造失败结果（不抛异常，供 execute 直接返回）。"""
        return cls(
            ok=False,
            output=message[:2000],
            error_code=error_code,
            data=data,
            bytes_out=len(message.encode("utf-8")),
        )


class ToolPreview(_ToolModel):
    """dry_run 预览结果。"""

    tool_name: str
    summary: str
    changes: list[dict[str, Any]] = Field(default_factory=list)
    safe: bool = True


class ProbeResult(_ToolModel):
    """幂等探测结果（**字段冻结**，ARCHITECTURE §7.3）。

    Attributes:
        executed: 外部世界是否已存在该副作用。
        external_ref: 已存在时的外部引用。
        confidence: 置信度（``high`` / ``medium`` / ``low`` / ``unknown``）。
        evidence: 探测证据文本。
    """

    executed: bool = False
    external_ref: str | None = None
    confidence: str = "unknown"
    evidence: str = ""


class StepIntent(_ToolModel):
    """一步意图（probe 的输入，WP4 复用）。

    Attributes:
        run_id: Run ID。
        task_id: Task ID。
        step_id: Step ID。
        idempotency_key: 幂等键。
        tool_name: 工具名。
        args: 工具参数（probe 依赖的关键字段从 args 取）。
        expected_sha256: ``write_file`` / ``edit_file`` / ``apply_patch`` 探测用。
        expected_external_ref: ``git_ops`` / ``create_pr`` 探测用（如分支名）。
        context: 附加上下文。
    """

    run_id: str = Field(min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=64)
    step_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(default="", max_length=128)
    tool_name: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    expected_sha256: str | None = None
    expected_external_ref: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 元类：强制 side_effect_class
# ---------------------------------------------------------------------------


class _ToolMeta(abc.ABCMeta):
    """强制具体工具类声明 :attr:`side_effect_class`。"""

    def __new__(mcls, name: str, bases: tuple[type, ...], namespace: dict[str, Any]) -> type:
        cls = super().__new__(mcls, name, bases, namespace)
        if not getattr(cls, "__abstractmethods__", None) and not namespace.get("abstract"):
            if not hasattr(cls, "side_effect_class") or getattr(cls, "side_effect_class") is None:
                raise TypeError(
                    f"工具 {name} 必须声明 side_effect_class"
                    "（PURE / IDEMPOTENT / PROBEABLE / UNSAFE 四选一）"
                )
        return cls


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class BaseTool(abc.ABC, metaclass=_ToolMeta):
    """工具抽象基类。

    子类必须声明：

    * :attr:`name` —— 工具名（冻结表见 §11.5）。
    * :attr:`description` —— 给模型看的说明。
    * :attr:`args_schema` —— 参数 Pydantic 模型。
    * :attr:`side_effect_class` —— 副作用分级（漏声明直接报错）。
    * :meth:`execute` —— 执行主体。
    * :meth:`probe` —— 幂等探测（``PURE`` 工具返回 ``executed=False`` 即可）。
    """

    #: 工具名（全仓唯一，冻结）。
    name: ClassVar[str] = ""

    #: 给模型看的说明。
    description: ClassVar[str] = ""

    #: 参数 Schema（Pydantic v2 模型类，不是实例）。
    args_schema: ClassVar[type[BaseModel]] = BaseModel

    #: 副作用分级（崩溃恢复决策依据）。
    side_effect_class: ClassVar[SideEffectClass] = None  # type: ignore[assignment]

    #: 是否需要在执行前显式确认。
    requires_confirmation: ClassVar[bool] = False

    # -- 冻结签名（§11.5） --------------------------------------------------

    @abc.abstractmethod
    async def execute(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        """执行工具。

        Args:
            ctx: 工具上下文。
            args: 已校验的参数（:attr:`args_schema` 实例）。

        Returns:
            :class:`ToolResult`；**失败也返回结果，不抛裸异常**。
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def probe(self, ctx: ToolContext, intent: StepIntent) -> ProbeResult:
        """探测外部世界是否已存在该副作用。

        Args:
            ctx: 工具上下文。
            intent: 步骤意图（含幂等键与期望值）。

        Returns:
            :class:`ProbeResult`。
        """
        raise NotImplementedError

    async def dry_run(self, ctx: ToolContext, args: BaseModel) -> ToolPreview:
        """预览执行效果（默认实现：什么都不做，返回安全预览）。

        Args:
            ctx: 工具上下文。
            args: 参数。

        Returns:
            :class:`ToolPreview`。
        """
        _ = ctx
        return ToolPreview(
            tool_name=self.name,
            summary=f"{self.name}：未提供 dry_run 预览",
            changes=[],
            safe=True,
        )

    def to_spec(self) -> ToolSpec:
        """导出给 LLM function calling 的工具声明。

        Returns:
            :class:`~loopforge.providers.types.ToolSpec`。
        """
        schema = self.args_schema.model_json_schema()
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=schema,
        )

    # -- 便捷方法 ------------------------------------------------------------

    def validate_args(self, raw: dict[str, Any]) -> BaseModel:
        """把原始字典校验成 :attr:`args_schema` 实例。

        Args:
            raw: LLM 传来的参数字典。

        Returns:
            校验后的参数模型。

        Raises:
            loopforge.core.errors.ValidationError: 参数非法。
        """
        return self.args_schema.model_validate(raw)

    @classmethod
    def is_pure(cls) -> bool:
        """是否无副作用。"""
        return cls.side_effect_class is SideEffectClass.PURE

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r}, side_effect={self.side_effect_class.value})"
