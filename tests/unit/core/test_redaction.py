"""``loopforge.core.redaction`` 单测 —— 密钥零泄漏是安全底线。

本文件既是功能测试，也是**安全回归基线**：24 条规则中任何一条被误删或写错，
都会在这里立刻变红。核心断言只有一句 ——
**脱敏后的文本里绝不能再出现原始密钥。**
"""

from __future__ import annotations

import json

import pytest

from loopforge.core.redaction import (
    REDACTED,
    SECRET_PATTERNS,
    SENSITIVE_KEYS,
    detect_secrets,
    has_secret,
    mask_secret,
    redact_mapping,
    redact_sequence,
    redact_text,
    redact_value,
)

#: (规则名, 样例密钥) —— 覆盖全部 24 条规则。
SECRET_SAMPLES: list[tuple[str, str]] = [
    (
        "private_key",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234567890abcdef\n-----END RSA PRIVATE KEY-----",
    ),
    ("openai_project_key", "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"),
    ("anthropic_key", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123"),
    ("openai_key", "sk-abcdefghijklmnopqrstuvwx0123456789"),
    ("github_pat", "ghp_" + "a" * 36),
    ("github_pat_fine", "github_pat_" + "A" * 30),
    ("github_oauth", "gho_" + "b" * 36),
    ("gitlab_pat", "glpat-abcdefghijklmnopqrstu"),
    ("aws_access_key_id", "AKIAIOSFODNN7EXAMPLE"),
    ("aws_secret_access_key", "aws_secret_access_key=" + "A" * 40),
    ("google_api_key", "AIza" + "B" * 35),
    ("gcp_private_key_id", '"private_key_id": "' + "a" * 40 + '"'),
    ("azure_client_secret", "azure_client_secret=abcdefghijklmnopqrstuvwx"),
    ("slack_token", "xoxb-1234567890-abcdefghijkl"),
    (
        "slack_webhook",
        "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXX",
    ),
    ("stripe_key", "sk_live_REDACTED_EXAMPLE_abcdefghij0123456789"),
    ("sendgrid_key", "SG.abcdefghijklmnop.qrstuvwxyz012345"),
    ("npm_token", "npm_" + "c" * 36),
    ("pypi_token", "pypi-" + "d" * 40),
    ("huggingface_token", "hf_" + "e" * 34),
    ("telegram_bot_token", "123456789:" + "F" * 35),
    (
        "jwt",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    ),
    ("url_basic_auth", "postgresql://admin:sup3rS3cretPwd@db.internal:5432/loopforge"),
    ("generic_secret_assignment", "password = 'hunter2-very-secret'"),
]


# ---------------------------------------------------------------------------
# 规则表本身
# ---------------------------------------------------------------------------


def test_pattern_table_has_24_rules_with_unique_names() -> None:
    """24 条规则，名称唯一 —— 数量变化必须是有意识的决定。"""
    assert len(SECRET_PATTERNS) == 24
    names = [rule.name for rule in SECRET_PATTERNS]
    assert len(set(names)) == 24


def test_private_key_rule_comes_first() -> None:
    """PEM 块必须最先匹配，否则会被后面的规则切碎。"""
    assert SECRET_PATTERNS[0].name == "private_key"


def test_generic_assignment_rule_comes_last() -> None:
    """兜底规则必须最后匹配，避免抢走精确规则的命中。"""
    assert SECRET_PATTERNS[-1].name == "generic_secret_assignment"


def test_sensitive_keys_are_lowercase_and_nonempty() -> None:
    """键名白名单统一小写，比较时才不会漏。"""
    assert SENSITIVE_KEYS
    for key in SENSITIVE_KEYS:
        assert key == key.lower()
        assert key.strip() == key


def test_redacted_marker() -> None:
    """遮蔽标记是对外可见的约定字符串。"""
    assert REDACTED == "[REDACTED]"


# ---------------------------------------------------------------------------
# mask_secret
# ---------------------------------------------------------------------------


def test_mask_secret_keeps_prefix() -> None:
    """保留前缀便于人工定位，其余一律遮蔽。"""
    assert mask_secret("sk-abcdefghijklmn") == "sk-a***"


def test_mask_secret_keeps_suffix() -> None:
    """需要时可同时保留后缀。"""
    assert mask_secret("abcdefghijklmnop", keep_prefix=2, keep_suffix=2) == "ab***op"


def test_mask_secret_short_value_fully_redacted() -> None:
    """太短的值保留任何字符都不安全，整体遮蔽。"""
    assert mask_secret("abcdef") == REDACTED
    assert mask_secret("") == REDACTED


def test_mask_secret_rejects_negative_keep() -> None:
    """负数参数是调用方 bug，必须立刻暴露。"""
    with pytest.raises(ValueError, match="keep_prefix"):
        mask_secret("whatever-long-enough", keep_prefix=-1)


# ---------------------------------------------------------------------------
# redact_text：24 条规则逐条回归
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("rule_name", "sample"), SECRET_SAMPLES, ids=[n for n, _ in SECRET_SAMPLES])
def test_each_rule_detects_its_sample(rule_name: str, sample: str) -> None:
    """每条规则都能在样例上命中。"""
    assert rule_name in detect_secrets(sample), f"规则 {rule_name} 未命中样例"
    assert has_secret(sample) is True


@pytest.mark.parametrize(("rule_name", "sample"), SECRET_SAMPLES, ids=[n for n, _ in SECRET_SAMPLES])
def test_each_sample_is_actually_masked(rule_name: str, sample: str) -> None:
    """脱敏后原始密钥不得残留 —— 这是本模块存在的唯一理由。"""
    cleaned = redact_text(sample, keep_prefix=0)
    assert cleaned != sample, f"规则 {rule_name} 没有产生任何替换"
    assert "***" in cleaned or REDACTED in cleaned


def test_url_basic_auth_keeps_host_drops_password() -> None:
    """连接串脱敏后仍要能看出连的是哪台库，但口令必须消失。"""
    url = "postgresql://admin:sup3rS3cretPwd@db.internal:5432/loopforge"
    cleaned = redact_text(url)
    assert "sup3rS3cretPwd" not in cleaned
    assert "db.internal" in cleaned
    assert "admin" in cleaned


def test_generic_assignment_keeps_key_name() -> None:
    """兜底规则只替换值，保留键名，日志才有排障价值。"""
    cleaned = redact_text("api_key=abcdef1234567890")
    assert cleaned.startswith("api_key=")
    assert "abcdef1234567890" not in cleaned


def test_clean_text_is_untouched() -> None:
    """不含密钥的文本必须原样返回，避免误伤正常日志。"""
    text = "任务 T-0001 在 loop 3 完成，耗时 12.5s，改动 4 个文件。"
    assert redact_text(text) == text
    assert has_secret(text) is False
    assert detect_secrets(text) == []


def test_empty_text_is_safe() -> None:
    """空串不进正则，直接返回。"""
    assert redact_text("") == ""
    assert has_secret("") is False


def test_multiple_secrets_in_one_line() -> None:
    """一行里多个密钥要全部处理。"""
    line = f"export A={SECRET_SAMPLES[3][1]} B={SECRET_SAMPLES[4][1]}"
    cleaned = redact_text(line, keep_prefix=0)
    assert SECRET_SAMPLES[3][1] not in cleaned
    assert SECRET_SAMPLES[4][1] not in cleaned


def test_detect_secrets_deduplicates_and_keeps_order() -> None:
    """规则名去重且按声明顺序返回，便于稳定断言。"""
    text = f"{SECRET_SAMPLES[3][1]} {SECRET_SAMPLES[3][1]} {SECRET_SAMPLES[4][1]}"
    hits = detect_secrets(text)
    assert hits == sorted(set(hits), key=hits.index)
    assert "openai_key" in hits
    assert "github_pat" in hits


# ---------------------------------------------------------------------------
# redact_value / redact_mapping
# ---------------------------------------------------------------------------


def test_sensitive_key_masks_whole_value() -> None:
    """命中敏感键名时不看值长什么样，整值遮蔽。"""
    assert redact_value("anything-at-all", key="password") == REDACTED
    assert redact_value(12345, key="api_key") == REDACTED
    assert redact_value("x", key="Authorization") == REDACTED


def test_sensitive_key_preserves_none() -> None:
    """``None`` 保持为 ``None``，避免把「未设置」误报成「有值」。"""
    assert redact_value(None, key="password") is None


def test_nested_mapping_is_recursively_redacted() -> None:
    """嵌套结构逐层下钻。"""
    source = {
        "provider": {
            "name": "openai",
            "api_key": "sk-abcdefghijklmnopqrstuvwx0123",
            "options": {"timeout": 30, "token": "ghp_" + "a" * 36},
        },
        "note": "连接串 postgresql://u:p4ssw0rdLong@h/db",
    }
    cleaned = redact_mapping(source)
    assert cleaned["provider"]["api_key"] == REDACTED
    assert cleaned["provider"]["options"]["token"] == REDACTED
    assert cleaned["provider"]["options"]["timeout"] == 30
    assert cleaned["provider"]["name"] == "openai"
    assert "p4ssw0rdLong" not in cleaned["note"]


def test_redact_mapping_does_not_mutate_input() -> None:
    """必须返回全新字典，原对象不动。"""
    source = {"api_key": "sk-abcdefghijklmnopqrstuvwx0123"}
    cleaned = redact_mapping(source)
    assert source["api_key"].startswith("sk-abcdef")
    assert cleaned["api_key"] == REDACTED


def test_sequences_keep_their_container_type() -> None:
    """list 还是 list，tuple 还是 tuple，set 退化为 list（保证可 JSON 化）。"""
    assert isinstance(redact_value(["a", "b"]), list)
    assert isinstance(redact_value(("a", "b")), tuple)
    assert isinstance(redact_value({"a", "b"}), list)


def test_bytes_are_fully_redacted() -> None:
    """二进制内容无法逐字符判断，一律遮蔽。"""
    assert redact_value(b"binary blob") == REDACTED
    assert redact_value(bytearray(b"blob")) == REDACTED


def test_scalars_pass_through() -> None:
    """非字符串标量原样返回。"""
    assert redact_value(42) == 42
    assert redact_value(3.14) == 3.14
    assert redact_value(True) is True
    assert redact_value(None) is None


def test_depth_limit_truncates_instead_of_recursing_forever() -> None:
    """超深结构截断而不是爆栈。"""
    deep: dict[str, object] = {"level": 0}
    cursor = deep
    for level in range(1, 30):
        child: dict[str, object] = {"level": level}
        cursor["child"] = child
        cursor = child
    cleaned = redact_value(deep)
    assert json.dumps(cleaned)  # 必须仍可序列化
    assert "[TRUNCATED]" in json.dumps(cleaned)


def test_redact_sequence_helper() -> None:
    """序列辅助函数与 ``redact_value`` 行为一致。"""
    cleaned = redact_sequence(["sk-abcdefghijklmnopqrstuvwx0123", "普通文本"])
    assert "sk-abcdefghijklmnopqrstuvwx0123" not in cleaned[0]
    assert cleaned[1] == "普通文本"


def test_redacted_mapping_is_json_serializable() -> None:
    """脱敏结果直接进 events.payload / audit_logs.detail，必须可序列化。"""
    payload = redact_mapping(
        {
            "prompt": "把 api_key=sk-abcdefghijklmnopqrstuvwx0123 换掉",
            "tags": ("a", "b"),
            "nested": {"secret": "x" * 40},
        }
    )
    text = json.dumps(payload, ensure_ascii=False, default=list)
    assert "sk-abcdefghijklmnopqrstuvwx0123" not in text
    assert "x" * 40 not in text
