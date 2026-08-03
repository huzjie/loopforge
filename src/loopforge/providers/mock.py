"""``MockProvider`` —— 确定性伪 LLM（**零 Key 端到端跑通的生命线**）。

设计目标（``TASK_BREAKDOWN`` §4.1 / §4.4）
-----------------------------------------
1. **不需要任何 API Key / 网络**，离线即可完整跑通 ``chat`` / ``chat_stream`` /
   ``count_tokens`` / ``health_check`` / 工具调用 / 多轮对话；
2. **字节级确定性**：相同 ``(role, loop_index, task_key, context_digest)`` 与
   相同消息序列，输出 100% 一致（DoD：连续 100 次相同入参输出完全一致）；
3. **能产出真实可用的代码**：根据 prompt 里的任务描述生成语法正确、可被
   ``pytest`` 实际执行通过的 Python 函数，并附带 unified diff；
4. **故障注入**：支持五类故障（超时 / 限流 / 服务端错误 / 鉴权失败 / 截断），
   供 WP4 恢复测试与 WP2 自身的重试链路测试使用。

确定性来源
----------
所有随机分支都从一个种子派生::

    seed = sha256(role | loop_index | task_key | context_digest
                  | model | messages_digest | tools_digest)[:16]

然后交给 :class:`random.Random(seed)`。禁止使用未播种的 ``random``、
未排序的 ``dict`` 迭代与系统时间。
"""

from __future__ import annotations

import difflib
import random
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any, ClassVar, Final

from loopforge.core.clock import Clock
from loopforge.core.hashing import canonical_json, sha256_hex
from loopforge.core.logging import get_logger
from loopforge.providers.base import HttpProviderBase
from loopforge.providers.token_counter import count_messages, count_text
from loopforge.providers.types import (
    ChatRequest,
    ChatResult,
    FaultSpec,
    Message,
    ProviderCapabilities,
    ProviderConfigModel,
    ProviderHealth,
    StreamChunk,
    ToolCallRequest,
    ToolSpec,
    Usage,
)

__all__ = [
    "MockProvider",
    "MockCodegen",
    "MockCodegenResult",
    "extract_function_name",
    "pick_template",
    "FUNCTION_TEMPLATES",
    "DEFAULT_MOCK_MODEL",
]

_LOG = get_logger(__name__)

#: Mock 默认模型名（与 config/pricing.yaml 的 mock 节对齐）。
DEFAULT_MOCK_MODEL: Final[str] = "mock-large"

#: 一次「确定性」输出的最大字符数（防止死循环生成超长内容）。
_MAX_OUTPUT_CHARS: Final[int] = 16_000


class MockCodegenResult:
    """一次代码生成的结果。

    Attributes:
        code: 生成的完整 Python 源码。
        diff: 与占位实现之间的 unified diff 文本。
        function_name: 生成的函数名。
        matched_template: 命中的模板名（未命中为 ``"generic"``）。
    """

    __slots__ = ("code", "diff", "function_name", "matched_template")

    def __init__(
        self, *, code: str, diff: str, function_name: str, matched_template: str
    ) -> None:
        """初始化结果。"""
        self.code = code
        self.diff = diff
        self.function_name = function_name
        self.matched_template = matched_template


#: 内置函数模板：``(签名, docstring, 函数体)``。全部为真实可运行的 Python。
FUNCTION_TEMPLATES: Final[dict[str, tuple[str, str, str]]] = {
    "factorial": (
        "def {name}(n: int) -> int:",
        "计算非负整数 n 的阶乘。",
        (
            "    if n < 0:\n"
            "        raise ValueError(\"n must be non-negative\")\n"
            "    result = 1\n"
            "    for i in range(2, n + 1):\n"
            "        result *= i\n"
            "    return result\n"
        ),
    ),
    "fibonacci": (
        "def {name}(n: int) -> int:",
        "返回第 n 个斐波那契数（F(0)=0, F(1)=1）。",
        (
            "    if n < 0:\n"
            "        raise ValueError(\"n must be non-negative\")\n"
            "    a, b = 0, 1\n"
            "    for _ in range(n):\n"
            "        a, b = b, a + b\n"
            "    return a\n"
        ),
    ),
    "is_palindrome": (
        "def {name}(s: str) -> bool:",
        "判断字符串是否为回文（忽略大小写与非字母数字字符）。",
        (
            "    cleaned = \"\".join(ch.lower() for ch in s if ch.isalnum())\n"
            "    return cleaned == cleaned[::-1]\n"
        ),
    ),
    "reverse_string": (
        "def {name}(s: str) -> str:",
        "反转字符串。",
        "    return s[::-1]\n",
    ),
    "sum_list": (
        "def {name}(nums: list[float]) -> float:",
        "返回数值列表的和，空列表返回 0.0。",
        (
            "    total = 0.0\n"
            "    for value in nums:\n"
            "        total += value\n"
            "    return total\n"
        ),
    ),
    "sort_numbers": (
        "def {name}(nums: list[float]) -> list[float]:",
        "返回升序排列的新列表，不修改入参。",
        "    return sorted(nums)\n",
    ),
    "count_words": (
        "def {name}(text: str) -> int:",
        "统计文本中的单词数量（按空白切分）。",
        "    return len(text.split()) if text else 0\n",
    ),
    "find_max": (
        "def {name}(nums: list[float]) -> float:",
        "返回列表最大值，空列表抛出 ValueError。",
        (
            "    if not nums:\n"
            "        raise ValueError(\"nums must not be empty\")\n"
            "    maximum = nums[0]\n"
            "    for value in nums[1:]:\n"
            "        if value > maximum:\n"
            "            maximum = value\n"
            "    return maximum\n"
        ),
    ),
    "binary_search": (
        "def {name}(nums: list[int], target: int) -> int:",
        "在升序列表中二分查找目标，返回下标，未找到返回 -1。",
        (
            "    low, high = 0, len(nums) - 1\n"
            "    while low <= high:\n"
            "        mid = (low + high) // 2\n"
            "        if nums[mid] == target:\n"
            "            return mid\n"
            "        if nums[mid] < target:\n"
            "            low = mid + 1\n"
            "        else:\n"
            "            high = mid - 1\n"
            "    return -1\n"
        ),
    ),
    "fizzbuzz": (
        "def {name}(n: int) -> list[str]:",
        "返回 1..n 的 FizzBuzz 序列。",
        (
            "    result = []\n"
            "    for i in range(1, n + 1):\n"
            "        if i % 15 == 0:\n"
            "            result.append(\"FizzBuzz\")\n"
            "        elif i % 3 == 0:\n"
            "            result.append(\"Fizz\")\n"
            "        elif i % 5 == 0:\n"
            "            result.append(\"Buzz\")\n"
            "        else:\n"
            "            result.append(str(i))\n"
            "    return result\n"
        ),
    ),
    "generic": (
        "def {name}(*args: Any, **kwargs: Any) -> Any:",
        "通用占位实现：返回 None。",
        (
            "    \"\"\"根据任务描述实现的通用函数。\"\"\"\n"
            "    # TODO(agent): 按任务描述补充实现\n"
            "    return None\n"
        ),
    ),
}

#: 模板关键词 → 模板名。命中按关键词出现次数累计。
_TEMPLATE_KEYWORDS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("factorial", ("factorial", "阶乘")),
    ("fibonacci", ("fibonacci", "斐波那契")),
    ("is_palindrome", ("palindrome", "回文")),
    ("reverse_string", ("reverse", "反转", "倒序")),
    ("sum_list", ("sum", "求和", "相加")),
    ("sort_numbers", ("sort", "排序")),
    ("count_words", ("word", "单词", "词数")),
    ("find_max", ("max", "最大")),
    ("binary_search", ("binary search", "二分", "binary_search")),
    ("fizzbuzz", ("fizzbuzz", "fizz buzz", "fizz-buzz")),
)

#: 函数名提取正则：``def name`` / ``function name`` / 中文描述。
_FUNC_NAME_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bdef\s+([A-Za-z_]\w*)\s*\(", re.IGNORECASE),
    re.compile(r"\bfunction\s+([A-Za-z_]\w*)\s*\(", re.IGNORECASE),
    re.compile(r"\b函数\s*[名称叫]?\s*[`'\"\u201c\u201d]?([A-Za-z_]\w*)", re.IGNORECASE),
    re.compile(r"\b(?:名为|叫)\s*([A-Za-z_]\w*)\s*的?\s*函数", re.IGNORECASE),
)


def extract_function_name(text: str) -> str:
    """从任务描述中提取候选函数名。

    Args:
        text: 任务描述文本。

    Returns:
        第一个命中的函数名；未命中返回空串。
    """
    for pattern in _FUNC_NAME_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return match.group(1)
    return ""


def pick_template(text: str, rng: random.Random) -> str:
    """按任务描述关键词挑选模板；无命中返回 ``"generic"``。

    关键词命中数量相同且不止一个时，用 ``rng`` 在并列项中确定选择
    （仍由种子决定，保证确定性）。

    Args:
        text: 任务描述。
        rng: 已播种的随机源。

    Returns:
        模板名。
    """
    lowered = (text or "").lower()
    best: list[str] = []
    best_score = 0
    for name, keywords in _TEMPLATE_KEYWORDS:
        score = sum(1 for kw in keywords if kw in lowered)
        if score > best_score:
            best, best_score = [name], score
        elif score == best_score and score > 0:
            best.append(name)
    if not best:
        return "generic"
    return rng.choice(sorted(best)) if len(best) > 1 else best[0]


def _placeholder_source(name: str) -> str:
    """构造占位实现（diff 的“旧版本”）。"""
    return f"def {name}(*args, **kwargs):\n    ...\n"


class MockCodegen:
    """确定性代码生成器 —— 为 MockProvider 提供「真实可运行」的代码。"""

    def generate(self, task: str, *, seed: int) -> MockCodegenResult:
        """按任务描述生成一段可运行的 Python 函数。

        Args:
            task: 任务描述（通常取最后一条用户消息）。
            seed: 派生随机种子。

        Returns:
            生成的代码 + unified diff。
        """
        rng = random.Random(seed)
        template_name = pick_template(task, rng)
        signature, docstring, body = FUNCTION_TEMPLATES[template_name]

        func_name = extract_function_name(task) or f"solution_{template_name}"
        code_lines = [
            '"""自动生成的示例实现（MockProvider 确定性输出）。"""',
            "",
            "from typing import Any",
            "",
            signature.format(name=func_name),
            f'    """{docstring}"""',
            body.rstrip("\n"),
            "",
        ]
        code = "\n".join(code_lines)
        old = _placeholder_source(func_name).splitlines(keepends=True)
        new = code.splitlines(keepends=True)
        diff = "".join(
            difflib.unified_diff(
                old,
                new,
                fromfile=f"before/{func_name}.py",
                tofile=f"after/{func_name}.py",
                lineterm="\n",
            )
        )
        return MockCodegenResult(
            code=code,
            diff=diff,
            function_name=func_name,
            matched_template=template_name,
        )


class MockProvider(HttpProviderBase):
    """确定性伪 LLM —— 无 Key、无网络、可复现、可注入故障。

    配置 ``options`` 约定键（``docs/guides/mock-mode.md``）：
    * ``faults``: 故障注入规则列表，每项为 :class:`FaultSpec` 的 dict；
    * ``response_style``: ``"text"`` / ``"code"`` / ``"auto"``（默认 auto，
      按任务描述是否含代码关键词自动决定）；
    * ``stream_chunk_size``: 流式分片大小（默认 8 字符）；
    * ``deterministic``: 是否强制确定性（默认 True）。
    """

    name: ClassVar[str] = "mock"
    aliases: ClassVar[tuple[str, ...]] = ("mockprovider", "fake", "stub")
    kind: ClassVar[str] = "mock"
    default_base_url: ClassVar[str] = ""
    default_model: ClassVar[str] = DEFAULT_MOCK_MODEL
    free: ClassVar[bool] = True

    def __init__(self, config: ProviderConfigModel, clock: Clock) -> None:
        """初始化 MockProvider。"""
        super().__init__(config, clock)
        self._call_index: int = 0
        self._faults: list[FaultSpec] = self._parse_faults(config)
        self._codegen = MockCodegen()

    # ------------------------------------------------------------------ 能力

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Mock 支持工具 / 流式 / JSON / seed，上下文窗口 131072。"""
        return ProviderCapabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_json_mode=True,
            supports_seed=True,
            context_window=131_072,
            max_output_tokens=16_384,
            supports_embedding=True,
        )

    # ------------------------------------------------------------------ 故障

    @staticmethod
    def _parse_faults(config: ProviderConfigModel) -> list[FaultSpec]:
        """把配置里的 ``faults`` 列表解析成 :class:`FaultSpec`。

        Args:
            config: Provider 配置。

        Returns:
            故障规则列表。
        """
        raw = config.option("faults")
        if not isinstance(raw, list):
            return []
        specs: list[FaultSpec] = []
        valid = {
            "auth",
            "rate_limit",
            "timeout",
            "server_error",
            "truncate",
            "content_filter",
            "bad_response",
        }
        for item in raw:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind", ""))
            if kind not in valid:
                continue
            specs.append(
                FaultSpec(
                    kind=kind,  # type: ignore[arg-type]
                    at_call=int(item.get("at_call", 1)),
                    times=int(item.get("times", 1)),
                    model=item.get("model"),
                    message=str(item.get("message", "")),
                )
            )
        return specs

    def _check_faults(self, request: ChatRequest) -> FaultSpec | None:
        """检查当前调用是否应触发故障。

        Args:
            request: 当前请求。

        Returns:
            命中的故障规则；未命中返回 ``None``。
        """
        model = self.resolve_model(request.model)
        for spec in self._faults:
            if spec.matches(call_index=self._call_index, model=model):
                spec.fire()
                return spec
        return None

    def _raise_fault(self, spec: FaultSpec, model: str) -> None:
        """把故障规则映射成对应异常（``truncate`` 不在此处理）。

        Args:
            spec: 命中的故障规则。
            model: 模型名。

        Raises:
            ProviderError: 归一化后的异常。
        """
        message = spec.message or f"MockProvider 注入故障 {spec.kind}"
        details = {"provider": self.name, "model": model, "fault": spec.kind}
        from loopforge.core.errors import (
            ProviderAuthError,
            ProviderRateLimitError,
            ProviderServerError,
            ProviderTimeoutError,
        )
        from loopforge.providers.base import (
            ProviderBadResponseError,
            ProviderContentFilteredError,
        )

        if spec.kind == "auth":
            raise ProviderAuthError(message, details=details)
        if spec.kind == "rate_limit":
            raise ProviderRateLimitError(
                message, details={**details, "retry_after_s": "1"}
            )
        if spec.kind == "timeout":
            raise ProviderTimeoutError(
                message, details={**details, "timeout_s": self._timeout_s()}
            )
        if spec.kind == "server_error":
            raise ProviderServerError(message, details=details)
        if spec.kind == "content_filter":
            raise ProviderContentFilteredError(message, details=details)
        if spec.kind == "bad_response":
            raise ProviderBadResponseError(message, details=details)
        raise ProviderServerError(f"未知故障种类：{spec.kind}", details=details)

    @staticmethod
    def _timeout_s() -> float:
        """返回当前配置的请求超时（Mock 故障注入用）。"""
        try:
            from loopforge.core.config import get_settings

            return float(get_settings().providers.request_timeout_s)
        except Exception:  # noqa: BLE001 —— 配置不可用时用缺省
            return 120.0

    # ------------------------------------------------------------------ 确定性

    def _derive_seed(self, request: ChatRequest) -> int:
        """从请求派生确定性种子。

        种子输入：``role`` / ``loop_index`` / ``task_key`` / ``context_digest``
        （metadata 约定键）+ 模型名 + 消息序列 + 工具声明。相同入参 → 相同种子。

        Args:
            request: 对话请求。

        Returns:
            非负整数种子。
        """
        messages_digest = canonical_json([m.model_dump() for m in request.messages])
        tools_digest = canonical_json([t.model_dump() for t in request.tools])
        material = "|".join(
            [
                request.meta("role", ""),
                request.meta("loop_index", ""),
                request.meta("task_key", ""),
                request.meta("context_digest", ""),
                self.resolve_model(request.model),
                messages_digest,
                tools_digest,
            ]
        )
        return int(sha256_hex(material)[:16], 16)

    # ------------------------------------------------------------------ 内容生成

    def _last_user_text(self, request: ChatRequest) -> str:
        """取最后一条用户消息文本（多轮对话的“当前问题”）。"""
        for message in reversed(request.messages):
            if message.role == "user":
                return message.text()
        return ""

    def _assistant_history(self, request: ChatRequest) -> list[str]:
        """取历史 assistant 消息文本（多轮对话的“前情提要”）。"""
        history: list[str] = []
        for message in request.messages:
            if message.role == "assistant" and message.text():
                history.append(message.text())
        return history

    def _style(self, request: ChatRequest) -> str:
        """决定输出风格：``code`` / ``text`` / ``json``。"""
        style = str(self.config.option("response_style", "auto")).strip().lower()
        if style in {"code", "text", "json"}:
            return style
        text = self._last_user_text(request)
        code_hint = any(
            kw in text.lower()
            for kw in ("def ", "function", "代码", "函数", "实现", "pytest", "write")
        )
        if request.tools and request.tool_choice in {"auto", "required"}:
            return "code"
        return "code" if code_hint else "text"

    def _render_response(self, request: ChatRequest, rng: random.Random) -> tuple[str, str]:
        """渲染确定性回复文本。

        Args:
            request: 对话请求。
            rng: 已播种的随机源。

        Returns:
            ``(content, matched_template)``。
        """
        text = self._last_user_text(request)
        style = self._style(request)
        history = self._assistant_history(request)

        if style == "code" or (style == "auto" and "def " in text.lower()):
            result = self._codegen.generate(text, seed=self._derive_seed(request))
            return result.code, result.matched_template

        if style == "json":
            payload = canonical_json(
                {
                    "status": "ok",
                    "role": request.meta("role", ""),
                    "loop_index": request.meta("loop_index", ""),
                    "task_key": request.meta("task_key", ""),
                    "digest": self._derive_seed(request),
                }
            )
            return payload, "json"

        # 文本风格：构造一段确定性的摘要回复。
        role = request.meta("role", "agent")
        loop_index = request.meta("loop_index", "0")
        task_key = request.meta("task_key", "unknown")
        preview = " ".join(text.split())[:120]
        lines = [
            f"[mock:{role}] 收到任务（loop={loop_index}, task={task_key}）。",
            f"任务描述摘要：{preview or '（空）'}",
        ]
        if history:
            lines.append(f"前文共 {len(history)} 轮，已接续上下文。")
        lines.append(
            f"确定性种子：{self._derive_seed(request):016x}（相同入参可复现）。"
        )
        return "\n".join(lines), "text"

    def _should_call_tool(self, request: ChatRequest) -> bool:
        """判断本轮是否应发起工具调用。"""
        if not request.tools:
            return False
        if request.tool_choice == "required":
            return True
        if request.tool_choice == "none":
            return False
        # auto：按任务文本是否提到工具名决定。
        text = self._last_user_text(request).lower()
        return any(spec.name.lower() in text for spec in request.tools)

    def _build_tool_call(
        self, request: ChatRequest, rng: random.Random
    ) -> ToolCallRequest:
        """确定性选择第一个「被提到」的工具并构造参数。

        参数值从工具的 JSON Schema 里抽样（``default`` → 类型样例），
        全部由 ``rng`` 决定，保证可复现。

        Args:
            request: 对话请求。
            rng: 已播种的随机源。

        Returns:
            一个工具调用。
        """
        text = self._last_user_text(request).lower()
        specs = list(request.tools)
        if request.tool_choice == "auto":
            mentioned = [s for s in specs if s.name.lower() in text]
            if mentioned:
                specs = mentioned
        spec = specs[0]
        arguments: dict[str, Any] = {}
        properties = spec.parameters.get("properties", {})
        if isinstance(properties, dict):
            for key, schema in properties.items():
                if not isinstance(schema, dict):
                    continue
                if "default" in schema:
                    arguments[key] = schema["default"]
                    continue
                schema_type = schema.get("type")
                if schema_type == "string":
                    arguments[key] = f"{key}_value"
                elif schema_type in {"integer", "number"}:
                    arguments[key] = rng.randint(1, 100)
                elif schema_type == "boolean":
                    arguments[key] = bool(rng.getrandbits(1))
                elif schema_type == "array":
                    arguments[key] = []
        return ToolCallRequest(
            id=f"mock_call_{self._call_index}",
            name=spec.name,
            arguments=arguments,
            raw_arguments=canonical_json(arguments),
        )

    # ------------------------------------------------------------------ 主入口

    async def chat(self, request: ChatRequest) -> ChatResult:
        """非流式对话补全（完全离线）。

        Args:
            request: 对话请求。

        Returns:
            完整结果。

        Raises:
            ProviderError: 注入的故障。
        """
        started = self.clock.monotonic()
        self._call_index += 1
        model = self.resolve_model(request.model)

        spec = self._check_faults(request)
        if spec is not None and spec.kind != "truncate":
            self._raise_fault(spec, model)
        truncate = spec is not None and spec.kind == "truncate"

        rng = random.Random(self._derive_seed(request))
        finish_reason: str = "stop"
        content = ""
        tool_calls: list[ToolCallRequest] = []

        if self._should_call_tool(request):
            tool_calls = [self._build_tool_call(request, rng)]
            finish_reason = "tool_calls"
        else:
            content, _ = self._render_response(request, rng)

        max_tokens = request.max_tokens or self.capabilities.max_output_tokens
        completion_tokens = count_text(content or "", model)
        if truncate:
            limit_chars = max(1, int(max_tokens * 3))
            if len(content) > limit_chars:
                content = content[:limit_chars]
            finish_reason = "length"

        prompt_tokens = count_messages(request.messages, model, tools=request.tools)
        return ChatResult(
            content=content,
            tool_calls=tool_calls,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=0,
            ),
            model=model,
            finish_reason=finish_reason,  # type: ignore[arg-type]
            latency_ms=self.elapsed_ms(started),
            raw={"mock": True, "seed": self._derive_seed(request)},
        )

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        """流式对话补全 —— 把 :meth:`chat` 的结果按确定性分片吐出。

        Args:
            request: 对话请求。

        Yields:
            :class:`StreamChunk` 增量片段。
        """
        started = self.clock.monotonic()
        self._call_index += 1
        model = self.resolve_model(request.model)

        spec = self._check_faults(request)
        if spec is not None and spec.kind != "truncate":
            self._raise_fault(spec, model)

        rng = random.Random(self._derive_seed(request))
        content = ""
        tool_calls: list[ToolCallRequest] = []
        finish_reason: str = "stop"

        if self._should_call_tool(request):
            tool_calls = [self._build_tool_call(request, rng)]
            finish_reason = "tool_calls"
        else:
            content, _ = self._render_response(request, rng)

        chunk_size = max(1, int(self.config.option("stream_chunk_size", 8)))
        for piece in _chunk_text(content, chunk_size):
            yield StreamChunk(delta=piece, index=0, raw={"mock": True})
        for call in tool_calls:
            yield StreamChunk(
                tool_call_delta=call, index=0, raw={"mock": True}
            )
        if spec is not None and spec.kind == "truncate":
            finish_reason = "length"
        prompt_tokens = count_messages(request.messages, model, tools=request.tools)
        completion_tokens = count_text(content or "", model)
        yield StreamChunk(
            finish_reason=finish_reason,  # type: ignore[arg-type]
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
            index=0,
            raw={"mock": True},
        )

    def count_tokens(self, messages: Sequence[Message], model: str) -> int:
        """估算一组消息的 prompt token 数（与全局估算器一致）。"""
        return count_messages(messages, model)

    async def health_check(self) -> ProviderHealth:
        """Mock 永远健康（除非配置了 ``auth`` 故障）。

        Returns:
            健康状态。
        """
        started = self.clock.monotonic()
        model = self.config.default_model or self.default_model
        healthy = True
        detail = "mock provider ready"
        error_code: str | None = None
        for spec in self._faults:
            if spec.kind == "auth" and spec.matches(
                call_index=1, model=model or ""
            ):
                healthy = False
                detail = spec.message or "mock auth fault"
                error_code = "LF-PROVIDER-0701"
        return ProviderHealth(
            name=self.name,
            healthy=healthy,
            latency_ms=self.elapsed_ms(started),
            checked_at=self.clock.now(),
            detail=detail,
            model=model,
            error_code=error_code,
        )

    async def embed(self, request: Any) -> Any:
        """Mock 向量化：按种子确定性生成 8 维向量。

        Args:
            request: 向量化请求（``EmbeddingRequest``）。

        Returns:
            确定性向量结果。
        """
        from loopforge.providers.types import EmbeddingRequest, EmbeddingResult

        req = request if isinstance(request, EmbeddingRequest) else None
        if req is None:
            return await super().embed(request)  # type: ignore[arg-type]
        started = self.clock.monotonic()
        vectors: list[list[float]] = []
        for text in req.inputs:
            seed = int(sha256_hex(text or "")[:16], 16)
            rng = random.Random(seed)
            vector = [round(rng.uniform(-1.0, 1.0), 6) for _ in range(8)]
            vectors.append(vector)
        return EmbeddingResult(
            vectors=vectors,
            model=req.model or self.resolve_model(None),
            usage=Usage(
                prompt_tokens=sum(count_text(t, req.model) for t in req.inputs),
                completion_tokens=0,
            ),
            latency_ms=self.elapsed_ms(started),
        )


def _chunk_text(text: str, size: int) -> list[str]:
    """把文本按固定长度切片（确定性，供流式使用）。"""
    if size <= 0:
        raise ValueError(f"chunk size 必须为正，收到 {size}")
    if not text:
        return []
    return [text[start : start + size] for start in range(0, len(text), size)]
