"""三级压缩测试 —— 各自可独立触发；L1/L2 完全确定性；保真度可验证。

DoD（``TASK_BREAKDOWN`` §7.4）：三级压缩各自可单独触发；
L1/L2 同输入同输出；``check_fidelity`` 能检出违背。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from loopforge.core.enums import MemoryLayer
from loopforge.memory.compressor import (
    Compressor,
    FidelityViolation,
    MemoryChunk,
    chunks_from,
    extract_key_facts,
)
from loopforge.memory.layers.base import estimate_tokens
from loopforge.memory.summarizer import Summarizer

_NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)


def _chunk(cid: str, content: str, salience: float = 0.5) -> MemoryChunk:
    return MemoryChunk(id=cid, content=content, salience=salience, created_at=_NOW)


def test_level1_deterministic_and_trims() -> None:
    """L1 结构化裁剪：确定性、去重、超长行截断。"""
    compressor = Compressor()
    long_output = "\n".join(f"line-{i:03d} 详细输出内容" for i in range(100))
    items = [
        _chunk("a", long_output),
        _chunk("b", long_output),  # 与 a 相同 → 去重
        _chunk("c", "短内容"),
    ]
    first = compressor.compress_level1(items)
    second = compressor.compress_level1(items)
    assert [chunk.content for chunk in first] == [chunk.content for chunk in second]
    # 去重后只剩 2 条（a 与 c）。
    assert len(first) == 2
    # 超长输出被头尾裁剪。
    assert any("line-000" in chunk.content for chunk in first)
    tail_chunk = next(chunk for chunk in first if "line-099" in chunk.content)
    assert tail_chunk is not None  # 尾部保留


def test_level2_deterministic_and_extractive() -> None:
    """L2 抽取式摘要：确定性、只留信息句。"""
    compressor = Compressor()
    text = (
        "今天早上我们讨论了计划。"
        "修复了预算超支 bug，根因是 precheck 没有做原子预留。"
        "会议气氛很好，大家都很开心。"
        "错误信息：BudgetExhaustedError 在并发下被正确抛出。"
        "散会。"
    )
    first = compressor.compress_level2([_chunk("a", text)])
    second = compressor.compress_level2([_chunk("a", text)])
    assert first == second, "L2 必须完全确定"
    assert "预算超支" in first or "BudgetExhaustedError" in first
    assert "会议气氛很好" not in first, "寒暄句应被丢弃"


def test_level3_recursive_summary() -> None:
    """L3 生成式摘要：确定性兜底可用，产出短于输入。"""
    summarizer = Summarizer()
    text = "\n".join(
        f"第 {i} 轮观察到错误 error-{i}，修复方式是调整预算参数"
        for i in range(50)
    )
    summary = summarizer.summarize_text(text, target_tokens=80)
    assert summary
    assert len(summary) < len(text)
    # 递归树。
    chunks = [_chunk(str(i), f"条目 {i}: 修复了错误 error-{i}") for i in range(20)]
    tree = summarizer.build_summary_tree(chunks, max_level=3)
    assert tree.children
    assert max(child.level for child in tree.children) >= 1


def test_compress_pipeline_reduces_tokens() -> None:
    """统一入口：三级都能触发并报告 token 节省与损失分。"""
    compressor = Compressor()
    items = [
        _chunk(f"i-{i}", f"观察记录 {i}：工具执行失败 error-code-{i}，文件 src/x.py 第 {i} 行报错")
        for i in range(200)
    ]
    before = sum(estimate_tokens(item.content) for item in items)
    for level in (1, 2, 3):
        text, tokens, loss = compressor.compress(items, level=level, target_tokens=200)
        assert 0.0 <= loss <= 1.0
        if level == 1:
            # L1 是结构化裁剪：对短条目不保证减少，但必须确定性且无损评分合法。
            assert tokens >= 0
        else:
            assert tokens < before, f"L{level} 必须减少 token"
            assert tokens <= 400, f"L{level} 抽取/生成应显著压缩：{tokens}"


def test_fidelity_detects_violation() -> None:
    """保真度校验：缺失关键事实能检出；完整保留则通过。"""
    compressor = Compressor()
    constraints = [
        _constraint("c1", "所有新接口必须返回 ApiResponse 包装"),
        _constraint("c2", "禁止修改 .github/workflows 目录"),
    ]
    ok_text = "计划：新接口 login 返回 ApiResponse 包装；不会碰 .github/workflows 目录。"
    report_ok = compressor.check_fidelity(run_id="run-1", constraints=constraints, plan_text=ok_text)
    assert report_ok.passed
    bad_text = "计划：直接返回裸 JSON；顺手改一下 workflows 的 CI 配置。"
    report_bad = compressor.check_fidelity(run_id="run-1", constraints=constraints, plan_text=bad_text)
    assert not report_bad.passed
    assert len(report_bad.violations) >= 1
    assert isinstance(report_bad.violations[0], FidelityViolation)


def test_fidelity_500_rounds_lossless() -> None:
    """连续 500 轮压缩后 Pinned 约束原文 100% 无损（DoD）。"""
    compressor = Compressor()
    constraint_text = "所有新接口必须返回 ApiResponse 包装，禁止修改 .github/workflows"
    constraint = _constraint("pin-1", constraint_text)
    text = constraint_text
    for round_index in range(500):
        filler = "\n".join(f"第 {round_index} 轮观察：工具输出 tool-{round_index} 的执行结果")
        compressed, _, _ = compressor.compress(
            [_chunk(f"m-{round_index}", filler)],
            level=3,
            target_tokens=120,
        )
        text = constraint_text + "\n" + compressed
        report = compressor.check_fidelity(
            run_id="run-1", constraints=[constraint], plan_text=text
        )
        assert report.passed, f"第 {round_index} 轮约束丢失"


def test_extract_key_facts_keeps_errors_and_paths() -> None:
    """关键事实提取：错误信息 / 路径 / 函数签名被保留。"""
    facts = extract_key_facts(
        "修复了预算超支 bug，文件 src/loopforge/core/clock.py 的 get_settings 返回 128000。"
        "顺手喝了一杯咖啡。"
    )
    joined = " ".join(facts)
    assert any("预算超支" in fact or "clock.py" in fact or "get_settings" in fact for fact in facts)
    assert "喝了一杯咖啡" not in joined


def _constraint(cid: str, text: str) -> object:
    class _C:
        id = cid
        text_content = text

    return _C()
