"""Provider 测试夹具 —— 对外提供 ``mock_provider`` fixture（WP4/WP5/WP6 共用）。

设计要点
--------
* ``mock_provider`` —— 一个**离线、确定性**的 :class:`MockProvider` 实例，
  任何 WP 的测试都可以直接声明形参使用，无需 API Key / 网络；
* ``mock_chat_request`` —— 构造一个带确定性 metadata 的 :class:`ChatRequest`，
  方便断言「相同入参 → 相同输出」；
* ``mock_code_request`` —— 带代码任务的请求（触发 MockCodegen）。

约定
----
fixture 使用 :func:`loopforge.core.clock.get_clock` 的当前时钟（conftest 默认
注入 :class:`FrozenClock`），因此测试之间天然隔离、可复现。
"""

from __future__ import annotations

from typing import Any

import pytest

from loopforge.providers.registry import create_mock_provider
from loopforge.providers.types import ChatRequest, Message, ToolSpec

__all__ = [
    "mock_provider",
    "mock_chat_request",
    "mock_code_request",
    "mock_tool_request",
    "mock_tool_spec",
]


@pytest.fixture
def mock_provider() -> Any:
    """一个可用的 MockProvider 实例（零 Key、离线、确定性）。"""
    return create_mock_provider()


@pytest.fixture
def mock_tool_spec() -> ToolSpec:
    """一个简单的工具声明。"""
    return ToolSpec(
        name="run_tests",
        description="运行测试并返回结果",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "测试文件路径"},
                "strict": {"type": "boolean", "default": True},
            },
            "required": ["path"],
        },
    )


@pytest.fixture
def mock_chat_request() -> ChatRequest:
    """一个带确定性 metadata 的普通对话请求。"""
    return ChatRequest(
        model="mock-large",
        messages=[
            Message.system("你是一个测试助手。"),
            Message.user("你好，请简单介绍你自己。"),
        ],
        metadata={
            "role": "SUMMARIZER",
            "loop_index": "0",
            "task_key": "fixture-task",
            "context_digest": "abc123",
        },
    )


@pytest.fixture
def mock_code_request() -> ChatRequest:
    """一个触发代码生成的任务请求。"""
    return ChatRequest(
        model="mock-large",
        messages=[
            Message.system("你是一个工程师。"),
            Message.user("请写一个 def factorial(n) 函数，计算阶乘。"),
        ],
        metadata={
            "role": "CODER",
            "loop_index": "0",
            "task_key": "code-task",
            "context_digest": "def456",
        },
    )


@pytest.fixture
def mock_tool_request(mock_tool_spec: ToolSpec) -> ChatRequest:
    """一个要求调用工具的任务请求（``tool_choice=required``）。"""
    return ChatRequest(
        model="mock-large",
        messages=[Message.user("请使用 run_tests 工具运行测试。")],
        tools=[mock_tool_spec],
        tool_choice="required",
        metadata={
            "role": "TESTER",
            "loop_index": "1",
            "task_key": "tool-task",
            "context_digest": "tool789",
        },
    )
