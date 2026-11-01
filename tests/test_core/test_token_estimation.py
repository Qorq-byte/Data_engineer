"""The C-level token estimator must stay arithmetically identical to the loop.

``PromptBuilder.estimate_tokens`` used to walk every character in Python. Prompts
carry a whole schema, so that loop cost ~1 s of a query at 1k tables and blocked
the event loop. The replacement counts the same character classes with two
C-level passes; these tests pin the arithmetic so a future refactor cannot
silently shift every budget and compaction threshold.
"""

from __future__ import annotations

import pytest

from app.core.prompt_builder import PromptBuilder


def reference_estimate(text: str) -> int:
    """The original per-character implementation, kept as the specification."""
    if not text:
        return 0
    en_chars = sum(1 for c in text if c.isascii() and not c.isspace())
    zh_chars = len(text) - en_chars
    return int(en_chars / 4 + zh_chars / 1.5)


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "SELECT 1",
        "SELECT * FROM users WHERE id = 1",
        "查询最近30天的订单金额",
        "用户 user_id 订单编号 order_no",
        "混合 mixed 内容 with 空格 and\ttabs\nand newlines",
        # ASCII control characters that Python counts as whitespace.
        "\t\n\v\f\r\x1c\x1d\x1e\x1f  x",
        # Non-ASCII whitespace is not ASCII, so the loop charged it as CJK.
        "全角\u3000空格\u00a0不换行",
        "emoji 😀 and 中文",
        "a" * 1000,
        "中" * 1000,
    ],
)
def test_estimate_matches_reference(text: str) -> None:
    assert PromptBuilder.estimate_tokens(text) == reference_estimate(text)


def test_estimate_matches_reference_on_mixed_bulk() -> None:
    block = "user_id INT, amount DECIMAL, 订单状态 VARCHAR, 创建时间 TIMESTAMP\n"
    text = block * 2000
    assert PromptBuilder.estimate_tokens(text) == reference_estimate(text)


def test_empty_text_is_zero() -> None:
    assert PromptBuilder.estimate_tokens("") == 0


def test_ascii_and_cjk_scaling() -> None:
    # 40 ASCII characters -> 10 tokens; 15 CJK characters -> 10 tokens.
    assert PromptBuilder.estimate_tokens("a" * 40) == 10
    assert PromptBuilder.estimate_tokens("中" * 15) == 10
