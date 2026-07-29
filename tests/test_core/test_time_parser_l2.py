"""Tests for TimeParser L2 LLM fallback — extract_with_fallback / _needs_llm_fallback."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.core.time_parser import TimeParser
from app.llm.router import LiteLLMRouter, RouterConfig
from app.models.query import TimeRange

# Fixed reference time for deterministic tests: 2026-07-15 14:30:00 (Wednesday)
REF_TIME = datetime(2026, 7, 15, 14, 30, 0)

# Texts with time-signal words that no L1 regex pattern matches
ZH_L1_MISS = "上上个季度的销售额"
EN_L1_MISS = "sales for the week before last"
# Text with no time reference at all
NO_TIME_TEXT = "list all users in region east"

VALID_JSON = '{"start": "2026-01-01", "end": "2026-03-31", "granularity": "quarter"}'


class FakeRouter:
    """Minimal stand-in for LiteLLMRouter — records calls, returns canned content."""

    def __init__(self, content: str = "", error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls = 0
        self.last_messages: list[dict[str, str]] | None = None

    async def complete(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> dict[str, Any]:
        self.calls += 1
        self.last_messages = messages
        if self.error is not None:
            raise self.error
        return {
            "choices": [{"message": {"role": "assistant", "content": self.content}}]
        }


# ═══════════════════════════════════════════════════════════════════════════
# _needs_llm_fallback
# ═══════════════════════════════════════════════════════════════════════════


class TestNeedsLlmFallback:
    @pytest.fixture
    def parser(self) -> TimeParser:
        return TimeParser()

    def test_zh_quarter_signal(self, parser):
        assert parser._needs_llm_fallback(ZH_L1_MISS) is True

    def test_zh_period_signal(self, parser):
        assert parser._needs_llm_fallback("环比增长的期间") is True

    def test_en_week_signal(self, parser):
        assert parser._needs_llm_fallback(EN_L1_MISS) is True

    def test_en_ago_signal(self, parser):
        assert parser._needs_llm_fallback("a few days ago") is True

    def test_no_signal_words(self, parser):
        assert parser._needs_llm_fallback(NO_TIME_TEXT) is False

    def test_empty_text(self, parser):
        assert parser._needs_llm_fallback("") is False


# ═══════════════════════════════════════════════════════════════════════════
# extract_with_fallback — L1 hit / gating
# ═══════════════════════════════════════════════════════════════════════════


class TestFallbackGating:
    async def test_l1_hit_zh_skips_llm(self):
        fake = FakeRouter(content=VALID_JSON)
        parser = TimeParser(router=fake)
        result = await parser.extract_with_fallback("昨天的订单", "zh", now=REF_TIME)
        assert result is not None
        assert result.raw_expression == "昨天"
        assert result.start == datetime(2026, 7, 14)
        assert fake.calls == 0

    async def test_l1_hit_en_skips_llm(self):
        fake = FakeRouter(content=VALID_JSON)
        parser = TimeParser(router=fake)
        result = await parser.extract_with_fallback(
            "show me last week numbers", "en", now=REF_TIME
        )
        assert result is not None
        assert result.unit == "week"
        assert fake.calls == 0

    async def test_no_router_returns_none(self):
        parser = TimeParser()  # router=None
        result = await parser.extract_with_fallback(ZH_L1_MISS, "zh", now=REF_TIME)
        assert result is None

    async def test_no_time_signal_skips_llm(self):
        fake = FakeRouter(content=VALID_JSON)
        parser = TimeParser(router=fake)
        result = await parser.extract_with_fallback(NO_TIME_TEXT, "en", now=REF_TIME)
        assert result is None
        assert fake.calls == 0

    async def test_default_now_l1_hit(self):
        """now=None default path works (uses datetime.now())."""
        parser = TimeParser(router=FakeRouter(content=VALID_JSON))
        result = await parser.extract_with_fallback("today", "en")
        assert result is not None
        assert result.unit == "day"

    def test_sync_extract_unaffected_by_router(self):
        """The sync L1 API keeps its exact signature and behaviour."""
        with_router = TimeParser(router=FakeRouter(content=VALID_JSON))
        without_router = TimeParser()
        a = with_router.extract("上个月的订单", "zh", reference_time=REF_TIME)
        b = without_router.extract("上个月的订单", "zh", reference_time=REF_TIME)
        assert a == b
        assert with_router.extract(ZH_L1_MISS, "zh", reference_time=REF_TIME) is None


# ═══════════════════════════════════════════════════════════════════════════
# extract_with_fallback — L2 LLM path
# ═══════════════════════════════════════════════════════════════════════════


class TestLlmExtraction:
    async def test_valid_json_returns_time_range(self):
        fake = FakeRouter(content=VALID_JSON)
        parser = TimeParser(router=fake)
        result = await parser.extract_with_fallback(ZH_L1_MISS, "zh", now=REF_TIME)
        assert isinstance(result, TimeRange)
        assert result.start == datetime(2026, 1, 1, 0, 0, 0)
        assert result.end == datetime(2026, 3, 31, 23, 59, 59, 999999)
        assert result.unit == "quarter"
        assert result.raw_expression == ZH_L1_MISS
        assert fake.calls == 1

    async def test_prompt_contains_text_and_reference_date(self):
        fake = FakeRouter(content=VALID_JSON)
        parser = TimeParser(router=fake)
        await parser.extract_with_fallback(ZH_L1_MISS, "zh", now=REF_TIME)
        assert fake.last_messages is not None
        prompt = fake.last_messages[0]["content"]
        assert ZH_L1_MISS in prompt
        assert "2026-07-15" in prompt

    async def test_json_wrapped_in_markdown_fence(self):
        fake = FakeRouter(content=f"```json\n{VALID_JSON}\n```")
        parser = TimeParser(router=fake)
        result = await parser.extract_with_fallback(ZH_L1_MISS, "zh", now=REF_TIME)
        assert result is not None
        assert result.unit == "quarter"

    async def test_missing_granularity_defaults_to_day(self):
        fake = FakeRouter(content='{"start": "2026-01-01", "end": "2026-03-31"}')
        parser = TimeParser(router=fake)
        result = await parser.extract_with_fallback(EN_L1_MISS, "en", now=REF_TIME)
        assert result is not None
        assert result.unit == "day"

    async def test_non_json_content_returns_none(self):
        fake = FakeRouter(content="Sorry, I cannot determine the time range.")
        parser = TimeParser(router=fake)
        result = await parser.extract_with_fallback(ZH_L1_MISS, "zh", now=REF_TIME)
        assert result is None

    async def test_malformed_json_returns_none(self):
        fake = FakeRouter(content='{"start": "2026-01-01", "end":}')
        parser = TimeParser(router=fake)
        result = await parser.extract_with_fallback(ZH_L1_MISS, "zh", now=REF_TIME)
        assert result is None

    async def test_missing_end_field_returns_none(self):
        fake = FakeRouter(content='{"start": "2026-01-01", "granularity": "day"}')
        parser = TimeParser(router=fake)
        result = await parser.extract_with_fallback(ZH_L1_MISS, "zh", now=REF_TIME)
        assert result is None

    async def test_null_fields_returns_none(self):
        fake = FakeRouter(content='{"start": null, "end": null, "granularity": null}')
        parser = TimeParser(router=fake)
        result = await parser.extract_with_fallback(ZH_L1_MISS, "zh", now=REF_TIME)
        assert result is None

    async def test_bad_date_format_returns_none(self):
        fake = FakeRouter(
            content='{"start": "2026/01/01", "end": "2026/03/31", "granularity": "day"}'
        )
        parser = TimeParser(router=fake)
        result = await parser.extract_with_fallback(ZH_L1_MISS, "zh", now=REF_TIME)
        assert result is None

    async def test_router_exception_returns_none(self):
        fake = FakeRouter(error=RuntimeError("provider down"))
        parser = TimeParser(router=fake)
        result = await parser.extract_with_fallback(ZH_L1_MISS, "zh", now=REF_TIME)
        assert result is None
        assert fake.calls == 1

    async def test_real_router_mock_mode_returns_none(self):
        """LiteLLMRouter mock mode returns non-JSON content → tolerant None."""
        router = LiteLLMRouter(RouterConfig(providers={}), mock_mode=True)
        parser = TimeParser(router=router)
        result = await parser.extract_with_fallback(ZH_L1_MISS, "zh", now=REF_TIME)
        assert result is None
        assert router.cost_tracker.total_calls == 1  # LLM was actually consulted
