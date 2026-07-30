"""Tests for Auto-compaction (estimate_tokens + ContextCompactor)."""

import copy

from app.core.prompt_builder import PromptBuilder
from app.harness.compaction import TRUNCATION_MARKER, ContextCompactor, estimate_tokens

# ── estimate_tokens ─────────────────────────────────────────────────────


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_english():
    # 40 ASCII chars, no spaces → ~4 chars/token → 10 tokens
    assert estimate_tokens("a" * 40) == 10


def test_estimate_tokens_chinese():
    # 30 CJK chars → ~1.5 chars/token → 20 tokens
    assert estimate_tokens("中" * 30) == 20


def test_estimate_tokens_mixed():
    # 8 ASCII (2 tokens) + 3 CJK (2 tokens) = int(2 + 2) = 4
    assert estimate_tokens("abcdefgh" + "中文字") == 4


def test_estimate_tokens_chinese_denser_than_english():
    """Same char count: CJK text costs more tokens than ASCII text."""
    assert estimate_tokens("中" * 100) > estimate_tokens("a" * 100)


def test_estimate_tokens_matches_prompt_builder():
    """Compaction must share the PromptBuilder estimation heuristic."""
    for text in ["hello world", "查询上个月的订单总额", "SELECT * FROM orders 订单表"]:
        assert estimate_tokens(text) == PromptBuilder.estimate_tokens(text)


# ── usage_ratio / should_compact ────────────────────────────────────────


def test_usage_ratio_empty_context():
    compactor = ContextCompactor(max_tokens=1000)
    assert compactor.usage_ratio({}) == 0.0


def test_usage_ratio_grows_with_content():
    compactor = ContextCompactor(max_tokens=1000)
    small = {"a": "x" * 100}
    large = {"a": "x" * 100, "b": "y" * 2000}
    assert compactor.usage_ratio(large) > compactor.usage_ratio(small) > 0.0


def test_usage_ratio_zero_max_tokens():
    compactor = ContextCompactor(max_tokens=0)
    assert compactor.usage_ratio({"a": "text"}) == 0.0


def test_should_compact_below_threshold():
    compactor = ContextCompactor(threshold=0.90, max_tokens=1000)
    # ~25 tokens out of 1000 → far below threshold
    assert compactor.should_compact({"a": "x" * 100}) is False


def test_should_compact_above_threshold():
    compactor = ContextCompactor(threshold=0.90, max_tokens=100)
    # "a"*400 alone is ~100 tokens → ratio >= 0.9
    assert compactor.should_compact({"data": "a" * 400}) is True


def test_custom_threshold():
    compactor = ContextCompactor(threshold=0.10, max_tokens=1000)
    # ~100 tokens / 1000 = 0.1 → triggers at the lowered threshold
    assert compactor.should_compact({"data": "a" * 400}) is True


# ── compact: strings ────────────────────────────────────────────────────


def test_compact_truncates_long_strings():
    compactor = ContextCompactor()
    long_text = "H" * 1000 + "T" * 1000
    result = compactor.compact({"log": long_text})
    compacted = result["log"]
    assert compacted.startswith("H" * 500)
    assert compacted.endswith("T" * 200)
    assert TRUNCATION_MARKER in compacted
    assert len(compacted) == 500 + len(TRUNCATION_MARKER) + 200


def test_compact_keeps_short_strings():
    compactor = ContextCompactor()
    result = compactor.compact({"sql": "SELECT 1"})
    assert result["sql"] == "SELECT 1"


def test_compact_reduces_tokens():
    compactor = ContextCompactor(max_tokens=1000)
    context = {"trace": "x" * 8000, "history": "y" * 8000}
    before = compactor.usage_ratio(context)
    after = compactor.usage_ratio(compactor.compact(context))
    assert after < before


def test_compact_custom_head_tail():
    compactor = ContextCompactor(head_chars=10, tail_chars=5)
    result = compactor.compact({"v": "A" * 50 + "B" * 50})
    assert result["v"] == "A" * 10 + TRUNCATION_MARKER + "B" * 5


# ── compact: keep_keys ──────────────────────────────────────────────────


def test_compact_keep_keys_preserved():
    compactor = ContextCompactor()
    long_text = "z" * 5000
    result = compactor.compact({"sql": long_text, "scratch": long_text}, keep_keys=["sql"])
    assert result["sql"] == long_text
    assert len(result["scratch"]) < len(long_text)


def test_compact_keep_keys_none_compacts_everything():
    compactor = ContextCompactor()
    result = compactor.compact({"a": "q" * 5000}, keep_keys=None)
    assert TRUNCATION_MARKER in result["a"]


def test_compact_keep_keys_preserves_lists_verbatim():
    compactor = ContextCompactor(max_list_items=2)
    items = list(range(50))
    result = compactor.compact({"rows": items}, keep_keys=["rows"])
    assert result["rows"] == items


# ── compact: lists / nested / scalars ───────────────────────────────────


def test_compact_list_keeps_recent_items():
    compactor = ContextCompactor(max_list_items=10)
    result = compactor.compact({"actions": list(range(50))})
    assert result["actions"] == list(range(40, 50))


def test_compact_short_list_unchanged():
    compactor = ContextCompactor(max_list_items=10)
    result = compactor.compact({"actions": [1, 2, 3]})
    assert result["actions"] == [1, 2, 3]


def test_compact_list_items_also_truncated():
    compactor = ContextCompactor(max_list_items=5)
    result = compactor.compact({"logs": ["short", "L" * 5000]})
    assert result["logs"][0] == "short"
    assert TRUNCATION_MARKER in result["logs"][1]


def test_compact_nested_dict():
    compactor = ContextCompactor()
    result = compactor.compact({"outer": {"inner": "n" * 5000, "count": 7}})
    assert TRUNCATION_MARKER in result["outer"]["inner"]
    assert result["outer"]["count"] == 7


def test_compact_scalars_unchanged():
    compactor = ContextCompactor()
    context = {"n": 42, "ratio": 0.5, "flag": True, "nothing": None}
    assert compactor.compact(context) == context


# ── compact: purity (no mutation) ───────────────────────────────────────


def test_compact_returns_new_dict():
    compactor = ContextCompactor()
    context = {"log": "m" * 5000}
    result = compactor.compact(context)
    assert result is not context


def test_compact_does_not_mutate_original():
    compactor = ContextCompactor(max_list_items=3)
    context = {
        "log": "m" * 5000,
        "actions": list(range(20)),
        "nested": {"deep": "d" * 5000},
    }
    snapshot = copy.deepcopy(context)
    compactor.compact(context, keep_keys=["log"])
    assert context == snapshot


# ── compact_async: summarizer injection ─────────────────────────────────


async def test_compact_async_without_summarizer_falls_back():
    compactor = ContextCompactor()
    result = await compactor.compact_async({"log": "f" * 5000})
    assert TRUNCATION_MARKER in result["log"]


async def test_compact_async_with_summarizer():
    async def summarizer(text: str) -> str:
        return f"SUMMARY({len(text)})"

    compactor = ContextCompactor(summarizer=summarizer)
    result = await compactor.compact_async({"log": "s" * 5000, "sql": "SELECT 1"})
    assert result["log"] == "SUMMARY(5000)"
    assert result["sql"] == "SELECT 1"  # short values are not summarized


async def test_compact_async_summarizer_respects_keep_keys():
    async def summarizer(text: str) -> str:
        return "SUMMARY"

    compactor = ContextCompactor(summarizer=summarizer)
    long_text = "k" * 5000
    result = await compactor.compact_async({"sql": long_text}, keep_keys=["sql"])
    assert result["sql"] == long_text
