"""Tests for SQLGenerator — multi-candidate SQL generation."""

import pytest

from app.core.prompt_builder import PromptBuilder
from app.core.sql_generator import SQLGenerator, _extract_sql, _fallback_candidate
from app.llm.router import LiteLLMRouter, RouterConfig
from app.models.domain import DomainConfig
from app.models.query import SQR, IntentType, QueryPair, SQLCandidate
from app.models.schema import SchemaSnapshot

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def router():
    return LiteLLMRouter(RouterConfig(providers={}), mock_mode=True)


@pytest.fixture
def generator(router):
    return SQLGenerator(router)


@pytest.fixture
def simple_sqr():
    return SQR(
        raw_text="查询所有订单",
        language="zh",
        intent=IntentType.SELECT,
    )


@pytest.fixture
def aggregate_sqr():
    return SQR(
        raw_text="统计上个月的销售额",
        language="zh",
        intent=IntentType.AGGREGATE,
        time_range=SQR.__dataclass_fields__["time_range"].default
        if False
        else None,
    )


# ═══════════════════════════════════════════════════════════════════════════
# _extract_sql helper
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractSql:
    def test_plain_sql(self):
        assert _extract_sql("SELECT * FROM orders") == "SELECT * FROM orders"

    def test_markdown_fence_sql(self):
        text = "```sql\nSELECT * FROM orders\n```"
        assert _extract_sql(text) == "SELECT * FROM orders"

    def test_markdown_fence_no_lang(self):
        text = "```\nSELECT 1\n```"
        assert _extract_sql(text) == "SELECT 1"

    def test_markdown_fence_postgresql(self):
        text = "```postgresql\nSELECT NOW()\n```"
        assert _extract_sql(text) == "SELECT NOW()"

    def test_here_is_prefix(self):
        text = "Here is the SQL query:\nSELECT * FROM users"
        assert _extract_sql(text) == "SELECT * FROM users"

    def test_heres_prefix(self):
        text = "Here's the SQL:\nSELECT 1"
        assert _extract_sql(text) == "SELECT 1"

    def test_sql_colon_prefix(self):
        text = "SQL:\nSELECT name FROM users"
        assert _extract_sql(text) == "SELECT name FROM users"

    def test_the_query_is_prefix(self):
        text = "The query is:\nSELECT COUNT(*) FROM orders"
        assert _extract_sql(text) == "SELECT COUNT(*) FROM orders"

    def test_empty_string(self):
        assert _extract_sql("") == ""

    def test_first_fence_wins(self):
        text = "```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```"
        assert _extract_sql(text) == "SELECT 1"


# ═══════════════════════════════════════════════════════════════════════════
# _fallback_candidate
# ═══════════════════════════════════════════════════════════════════════════


class TestFallbackCandidate:
    def test_returns_candidate_with_zero_confidence(self, simple_sqr):
        c = _fallback_candidate(simple_sqr)
        assert isinstance(c, SQLCandidate)
        assert c.confidence == 0.0
        assert c.generation_mode == "fallback"
        assert simple_sqr.raw_text in c.sql_text


# ═══════════════════════════════════════════════════════════════════════════
# SQLGenerator — temperature sampling (mock mode)
# ═══════════════════════════════════════════════════════════════════════════


class TestTemperatureSampling:
    async def test_generate_single_candidate(self, generator, simple_sqr):
        candidates = await generator.generate(simple_sqr, num_candidates=1)
        assert len(candidates) == 1
        assert isinstance(candidates[0], SQLCandidate)
        assert candidates[0].generation_mode == "primary"

    async def test_generate_multiple_candidates(self, generator, simple_sqr):
        candidates = await generator.generate(simple_sqr, num_candidates=3)
        assert len(candidates) == 3
        modes = {c.generation_mode for c in candidates}
        assert "primary" in modes
        assert "alt_1" in modes
        assert "alt_2" in modes

    async def test_confidence_decreases(self, generator, simple_sqr):
        candidates = await generator.generate(simple_sqr, num_candidates=3)
        for i in range(len(candidates) - 1):
            assert candidates[i].confidence >= candidates[i + 1].confidence

    async def test_candidates_have_unique_ids(self, generator, simple_sqr):
        candidates = await generator.generate(simple_sqr, num_candidates=3)
        ids = {c.id for c in candidates}
        assert len(ids) == 3

    async def test_model_in_candidate(self, generator, simple_sqr):
        candidates = await generator.generate(simple_sqr, model="claude-sonnet-4")
        assert candidates[0].model

    async def test_dialect_in_candidate(self, generator, simple_sqr):
        candidates = await generator.generate(simple_sqr, dialect="postgresql")
        assert candidates[0].dialect == "postgresql"

    async def test_num_candidates_clamped(self, generator, simple_sqr):
        """n values outside 1-5 should be clamped."""
        # n=0 → should still return at least 1
        candidates = await generator.generate(simple_sqr, num_candidates=0)
        assert len(candidates) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# SQLGenerator — multi-perspective (mock mode)
# ═══════════════════════════════════════════════════════════════════════════


class TestMultiPerspective:
    async def test_multi_perspective_generates_candidates(self, generator, simple_sqr):
        candidates = await generator.generate(
            simple_sqr, num_candidates=2, strategy="multi_perspective",
        )
        assert len(candidates) == 2

    async def test_multi_perspective_has_reasoning(self, generator, simple_sqr):
        candidates = await generator.generate(
            simple_sqr, num_candidates=2, strategy="multi_perspective",
        )
        # At least the primary should have reasoning
        assert any(c.reasoning for c in candidates)

    async def test_multi_perspective_max_3(self, generator, simple_sqr):
        """Only 3 perspectives defined, so n > 3 caps at 3."""
        candidates = await generator.generate(
            simple_sqr, num_candidates=5, strategy="multi_perspective",
        )
        assert len(candidates) <= 3


# ═══════════════════════════════════════════════════════════════════════════
# SQLGenerator — streaming (mock mode)
# ═══════════════════════════════════════════════════════════════════════════


class TestStreaming:
    async def test_stream_yields_tokens(self, generator, simple_sqr):
        chunks = []
        async for chunk in generator.generate_stream(simple_sqr):
            chunks.append(chunk)
        assert len(chunks) > 0
        assert all(isinstance(c, str) for c in chunks)

    async def test_stream_with_model(self, generator, simple_sqr):
        chunks = []
        async for chunk in generator.generate_stream(simple_sqr, model="claude-haiku-4-5"):
            chunks.append(chunk)
        assert len(chunks) > 0


# ═══════════════════════════════════════════════════════════════════════════
# SQLGenerator — with custom PromptBuilder
# ═══════════════════════════════════════════════════════════════════════════


class TestWithCustomPromptBuilder:
    async def test_custom_prompt_builder_used(self, router, simple_sqr):
        custom = PromptBuilder(dialect="mysql")
        gen = SQLGenerator(router, prompt_builder=custom)
        assert gen.prompt_builder is custom
        candidates = await gen.generate(simple_sqr)
        assert len(candidates) >= 1

    async def test_default_prompt_builder_created(self, router):
        gen = SQLGenerator(router)
        assert gen.prompt_builder is not None
        assert isinstance(gen.prompt_builder, PromptBuilder)


# ═══════════════════════════════════════════════════════════════════════════
# Integration: SQLGenerator with schema and domain
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegration:
    async def test_with_schema(self, generator):
        from app.models.schema import ColumnSchema, TableSchema

        schema = SchemaSnapshot(
            database_type="postgresql",
            database_name="test",
            tables={
                "orders": TableSchema(
                    name="orders",
                    columns=[
                        ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                        ColumnSchema(name="amount", type="DECIMAL"),
                    ],
                ),
            },
        )
        sqr = SQR(raw_text="find orders", language="en", intent=IntentType.SELECT)
        candidates = await generator.generate(sqr, schema=schema)
        assert len(candidates) >= 1

    async def test_with_domain(self, generator):
        from app.models.domain import BusinessRule, GlossaryTerm, TermMapping

        domain = DomainConfig(
            name="ecommerce",
            glossary=[
                GlossaryTerm(
                    term="订单",
                    term_en="order",
                    mapping=TermMapping(expression="orders"),
                ),
            ],
            rules=[
                BusinessRule(id="R1", description="No cancelled orders"),
            ],
        )
        sqr = SQR(raw_text="查询所有订单", language="zh", intent=IntentType.SELECT)
        candidates = await generator.generate(sqr, domain=domain)
        assert len(candidates) >= 1

    async def test_with_few_shot_examples(self, generator):
        sqr = SQR(raw_text="统计上个月的订单", language="zh", intent=IntentType.AGGREGATE)
        pairs = [
            QueryPair(
                nl_text="统计本周的订单",
                sql_text=(
                    "SELECT COUNT(*) FROM orders "
                    "WHERE created_at >= date_trunc('week', CURRENT_DATE)"
                ),
                similarity=0.85,
            ),
        ]
        candidates = await generator.generate(sqr, similar_pairs=pairs)
        assert len(candidates) >= 1
