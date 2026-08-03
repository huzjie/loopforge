"""红线用例召回率测试 —— 200+ 条用例，召回率 100%，误报率 < 5%。

DoD（``TASK_BREAKDOWN`` §7.4）：``test_redlines.py`` 200+ 条红线用例
召回率 100%，误报率 < 5%。
"""

from __future__ import annotations

import pytest

from loopforge.governance.policy.engine import PolicyEngine
from loopforge.governance.policy.loader import PolicyLoader
from loopforge.governance.policy.redlines import RedlineChecker, load_redlines

pytestmark = pytest.mark.asyncio


async def test_redline_file_has_200_plus_cases() -> None:
    """红线用例文件 >= 200 条。"""
    cases = load_redlines()
    assert len(cases) >= 200, f"红线用例只有 {len(cases)} 条"


async def test_redline_recall_100_percent() -> None:
    """危险用例召回率 100%。"""
    engine = PolicyEngine(rules=PolicyLoader().load("default"))
    cases = load_redlines()
    report = await RedlineChecker(engine, cases=cases).check()
    assert report.recall_rate == 1.0, f"召回率 {report.recall_rate:.2%}，失败：{report.failures[:5]}"


async def test_redline_false_positive_below_5_percent() -> None:
    """安全用例误报率 < 5%。"""
    engine = PolicyEngine(rules=PolicyLoader().load("default"))
    cases = load_redlines()
    report = await RedlineChecker(engine, cases=cases).check()
    assert report.false_positive_rate < 0.05, f"误报率 {report.false_positive_rate:.2%}"


async def test_redline_all_cases_structured() -> None:
    """全部用例都能转成 PolicyAction 且字段合法。"""
    cases = load_redlines()
    for case in cases:
        action = case.to_action()
        assert action.kind in (
            "command",
            "file_write",
            "file_read",
            "network",
            "git",
            "dependency",
            "secret",
        )
        assert case.expected in ("DENY", "ALLOW", "REQUIRE_APPROVAL")


async def test_strict_policy_also_passes_redlines() -> None:
    """严格策略在危险用例上同样 100% 召回（安全用例可能更严格）。"""
    engine = PolicyEngine(rules=PolicyLoader().load("strict"))
    cases = load_redlines()
    report = await RedlineChecker(engine, cases=cases).check()
    assert report.recall_rate == 1.0
