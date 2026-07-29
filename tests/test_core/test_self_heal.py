"""Tests for SelfHealingRetry — error analysis + prompt correction + retry loop."""

import pytest

from app.core.self_heal import (
    ErrorAnalysis,
    ErrorCategory,
    SelfHealingRetry,
    _analyse_errors,
    _build_fix_instructions,
)
from app.models.query import SQR, IntentType, SQLCandidate, ValidationReport

pytestmark = pytest.mark.anyio


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def healer():
    return SelfHealingRetry()


# ═══════════════════════════════════════════════════════════════════════════
# ErrorAnalysis
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorAnalysis:
    def test_empty(self):
        a = ErrorAnalysis()
        assert a.total_errors == 0
        assert not a.is_blocking

    def test_syntax_is_blocking(self):
        a = ErrorAnalysis(syntax_errors=["SYNTAX_ERROR: bad SQL"])
        assert a.is_blocking
        assert a.total_errors == 1

    def test_schema_is_blocking(self):
        a = ErrorAnalysis(schema_errors=["SCHEMA_ERROR: table not found"])
        assert a.is_blocking

    def test_type_not_blocking(self):
        a = ErrorAnalysis(type_errors=["TYPE_WARNING: comparing INT with VARCHAR"])
        assert not a.is_blocking
        assert a.total_errors == 1

    def test_summary(self):
        a = ErrorAnalysis(
            syntax_errors=["bad syntax"],
            schema_errors=["missing table"],
        )
        summary = a.summary()
        assert "Syntax" in summary
        assert "Schema" in summary

    def test_summary_empty(self):
        assert ErrorAnalysis().summary() == "No errors"


# ═══════════════════════════════════════════════════════════════════════════
# ErrorCategory
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorCategory:
    def test_constants(self):
        assert ErrorCategory.SYNTAX == "syntax"
        assert ErrorCategory.SCHEMA == "schema"
        assert ErrorCategory.TYPE == "type"
        assert ErrorCategory.EXECUTION == "execution"


# ═══════════════════════════════════════════════════════════════════════════
# _analyse_errors
# ═══════════════════════════════════════════════════════════════════════════


class TestAnalyseErrors:
    def test_classifies_syntax(self):
        a = _analyse_errors(["SYNTAX_ERROR: bad"], None)
        assert len(a.syntax_errors) == 1

    def test_classifies_schema(self):
        a = _analyse_errors(
            ["SCHEMA_ERROR: Table 'x' not found",
             "SCHEMA_ERROR: Column 'y' not found"],
            None,
        )
        assert len(a.schema_errors) == 2

    def test_classifies_type(self):
        a = _analyse_errors(["TYPE_WARNING: comparing INT with VARCHAR"], None)
        assert len(a.type_errors) == 1

    def test_classifies_mixed(self):
        a = _analyse_errors(
            ["SYNTAX_ERROR: parse failed",
             "SCHEMA_ERROR: table missing",
             "TYPE_WARNING: type mismatch"],
            None,
        )
        assert a.total_errors == 3
        assert len(a.syntax_errors) == 1
        assert len(a.schema_errors) == 1
        assert len(a.type_errors) == 1

    def test_classifies_unknown(self):
        a = _analyse_errors(["Something weird happened"], None)
        assert len(a.unknown_errors) == 1


# ═══════════════════════════════════════════════════════════════════════════
# _build_fix_instructions
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildFixInstructions:
    def test_syntax_instructions(self):
        a = ErrorAnalysis(syntax_errors=["bad"])
        text = _build_fix_instructions(a)
        assert "SYNTAX" in text

    def test_schema_instructions(self):
        a = ErrorAnalysis(schema_errors=["bad"])
        text = _build_fix_instructions(a)
        assert "SCHEMA" in text

    def test_type_instructions(self):
        a = ErrorAnalysis(type_errors=["bad"])
        text = _build_fix_instructions(a)
        assert "TYPE" in text

    def test_execution_instructions(self):
        a = ErrorAnalysis(execution_errors=["bad"])
        text = _build_fix_instructions(a)
        assert "EXECUTION" in text

    def test_empty_fallback(self):
        text = _build_fix_instructions(ErrorAnalysis())
        assert len(text) > 0


# ═══════════════════════════════════════════════════════════════════════════
# SelfHealingRetry — with mock generator + validator
# ═══════════════════════════════════════════════════════════════════════════


class TestRetryWithMocks:
    @pytest.fixture
    def mock_generator(self):
        class MockGen:
            def __init__(self, sqls: list[str]):
                self.sqls = sqls
                self.calls = 0

            async def generate(self, sqr, **kwargs):
                idx = min(self.calls, len(self.sqls) - 1)
                sql = self.sqls[idx]
                self.calls += 1
                return [SQLCandidate(
                    id=f"mock_{idx}",
                    sql_text=sql,
                    confidence=0.8,
                    generation_mode="test",
                )]

        return MockGen

    @pytest.fixture
    def mock_validator(self):
        class MockVal:
            def __init__(self, results: list[bool]):
                self.results = results
                self.calls = 0

            async def validate(self, sql, **kwargs):
                idx = min(self.calls, len(self.results) - 1)
                passed = self.results[idx]
                self.calls += 1
                if passed:
                    return ValidationReport(passed=True, score=1.0)
                return ValidationReport(
                    passed=False,
                    score=0.0,
                    syntax_errors=[] if idx > 0 else ["SYNTAX_ERROR: test"],
                    schema_errors=["SCHEMA_ERROR: test"] if idx > 0 else [],
                )

        return MockVal

    async def test_passes_first_retry(self, mock_generator, mock_validator):
        """First retry should succeed."""
        gen = mock_generator([
            "SELECT * FROM wrong_table",
            "SELECT * FROM orders WHERE status = 'active'",
        ])
        val = mock_validator([False, True])

        healer = SelfHealingRetry(generator=gen, validator=val)
        sqr = SQR(raw_text="find orders", language="en", intent=IntentType.SELECT)
        result = await healer.retry_until_valid(
            sqr, "SELECT * FROM wrong_table",
            ["SCHEMA_ERROR: Table 'wrong_table' not found"],
        )
        assert result is not None
        assert result.generation_mode == "self_heal"
        assert "orders" in result.sql_text

    async def test_exhausts_all_rounds(self, mock_generator, mock_validator):
        """All 3 rounds fail → None."""
        bad_sqls = [
            "SELECT * FROM bad1",
            "SELECT * FROM bad2",
            "SELECT * FROM bad3",
        ]
        gen = mock_generator(bad_sqls)
        val = mock_validator([False, False, False])

        healer = SelfHealingRetry(generator=gen, validator=val)
        sqr = SQR(raw_text="test", language="en", intent=IntentType.SELECT)
        result = await healer.retry_until_valid(
            sqr, "SELECT * FROM bad0", ["SYNTAX_ERROR: bad"],
        )
        assert result is None
        assert len(healer.history) == 3

    async def test_no_generator_returns_none(self, healer):
        sqr = SQR(raw_text="test", language="en", intent=IntentType.SELECT)
        result = await healer.retry_until_valid(
            sqr, "SELECT 1", ["error"],
        )
        assert result is None

    async def test_no_validator_accepts_first(self, mock_generator):
        """Without validator, the first correction is accepted."""
        gen = mock_generator(["SELECT * FROM orders"])
        healer = SelfHealingRetry(generator=gen, validator=None)
        sqr = SQR(raw_text="test", language="en", intent=IntentType.SELECT)
        result = await healer.retry_until_valid(
            sqr, "SELECT * FROM bad", ["error"],
        )
        assert result is not None
        assert result.sql_text == "SELECT * FROM orders"

    async def test_history_recorded(self, mock_generator, mock_validator):
        gen = mock_generator(["SELECT * FROM orders"])
        val = mock_validator([True])

        healer = SelfHealingRetry(generator=gen, validator=val)
        sqr = SQR(raw_text="test", language="en", intent=IntentType.SELECT)
        await healer.retry_until_valid(
            sqr, "SELECT * FROM bad", ["SCHEMA_ERROR"],
        )
        assert len(healer.history) == 1
        assert healer.history[0]["result"] == "passed"

    async def test_string_error_converted_to_list(self, mock_generator, mock_validator):
        gen = mock_generator(["SELECT * FROM orders"])
        val = mock_validator([True])

        healer = SelfHealingRetry(generator=gen, validator=val)
        sqr = SQR(raw_text="test", language="en", intent=IntentType.SELECT)
        result = await healer.retry_until_valid(
            sqr, "SELECT * FROM bad", "SCHEMA_ERROR: table x missing",
        )
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════
# SelfHealingRetry — correction prompt
# ═══════════════════════════════════════════════════════════════════════════


class TestCorrectionPrompt:
    def test_prompt_contains_error_info(self, healer):
        analysis = _analyse_errors(
            ["SCHEMA_ERROR: Table 'x' not found"], None
        )
        prompt = healer._build_error_context(
            "find orders", "SELECT * FROM x", ["SCHEMA_ERROR: Table 'x' not found"],
            analysis,
        )
        # Error context contains failed SQL and error info (NL question is
        # passed separately via sqr.raw_text to PromptBuilder).
        assert "SCHEMA_ERROR" in prompt
        assert "SELECT * FROM x" in prompt
        assert "Failed SQL" in prompt

    def test_prompt_has_fix_instructions(self, healer):
        analysis = _analyse_errors(
            ["SYNTAX_ERROR: parse error"], None
        )
        prompt = healer._build_error_context(
            "test", "BAD SQL", ["SYNTAX_ERROR: parse error"],
            analysis,
        )
        assert "SYNTAX" in prompt

    def test_prompt_truncates_many_errors(self, healer):
        analysis = _analyse_errors(
            [f"error {i}" for i in range(20)], None
        )
        prompt = healer._build_error_context(
            "test", "SQL", [f"error {i}" for i in range(20)],
            analysis,
        )
        # Should only show first 5 errors
        assert "error 6" not in prompt


# ═══════════════════════════════════════════════════════════════════════════
# SelfHealingRetry — MAX_ROUNDS
# ═══════════════════════════════════════════════════════════════════════════


class TestMaxRounds:
    def test_max_rounds_is_3(self):
        assert SelfHealingRetry.MAX_ROUNDS == 3

    def test_can_override(self):
        class CustomHealer(SelfHealingRetry):
            MAX_ROUNDS = 5

        assert CustomHealer.MAX_ROUNDS == 5


