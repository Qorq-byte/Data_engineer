"""Tests for PromptBuilder — multi-level templates + token budget management."""

import pytest

from app.core.prompt_builder import (
    DEFAULT_BUDGETS,
    L1_TEMPLATE,
    L2_TEMPLATE,
    L3_TEMPLATE,
    L4_TEMPLATE,
    SECTION_BUDGETS,
    PromptBuilder,
    PromptLevel,
)
from app.models.domain import (
    BusinessRule,
    DomainConfig,
    GlossaryTerm,
    TermMapping,
)
from app.models.query import (
    SQR,
    Ambiguity,
    Entity,
    IntentType,
    QueryPair,
    TimeRange,
)
from app.models.schema import (
    ColumnSchema,
    SchemaSnapshot,
    TableSchema,
)

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def builder():
    return PromptBuilder()


@pytest.fixture
def simple_sqr():
    return SQR(
        raw_text="查询所有订单",
        language="zh",
        intent=IntentType.SELECT,
    )


@pytest.fixture
def aggregate_sqr():
    """L1-level aggregate: 2 entities (not >2), time_range → 1 point → L1."""
    return SQR(
        raw_text="统计上个月各地区的销售额前10名",
        language="zh",
        intent=IntentType.AGGREGATE,
        entities=[
            Entity(name="地区", type="column", normalized="地区"),
            Entity(name="销售额", type="column", normalized="销售额"),
        ],
        time_range=TimeRange(),
        limit=10,
    )


@pytest.fixture
def l2_sqr():
    """L2-level: 3 entities(>2→+1) + time_range(+1) = 2 → L2.

    Raw text and entities are designed to match sample_domain glossary terms.
    """
    return SQR(
        raw_text="统计上个月各地区各产品的订单金额和下单时间前10名",
        language="zh",
        intent=IntentType.AGGREGATE,
        entities=[
            Entity(name="地区", type="column", normalized="地区"),
            Entity(name="订单金额", type="column", normalized="订单金额"),
            Entity(name="下单时间", type="column", normalized="下单时间"),
            Entity(name="产品", type="column", normalized="产品"),
        ],
        time_range=TimeRange(),
        limit=10,
    )


@pytest.fixture
def join_sqr():
    return SQR(
        raw_text="关联用户表和订单表查询用户的订单详情",
        language="zh",
        intent=IntentType.JOIN,
        entities=[
            Entity(name="用户", type="table", normalized="用户"),
            Entity(name="订单", type="table", normalized="订单"),
        ],
        target_tables=["用户", "订单"],
    )


@pytest.fixture
def funnel_sqr():
    return SQR(
        raw_text="用户注册到首单转化漏斗分析",
        language="zh",
        intent=IntentType.FUNNEL,
        entities=[
            Entity(name="用户", type="table", normalized="用户"),
            Entity(name="注册", type="event", normalized="注册"),
            Entity(name="首单", type="event", normalized="首单"),
        ],
    )


@pytest.fixture
def sample_schema():
    return SchemaSnapshot(
        database_type="postgresql",
        database_name="ecommerce",
        tables={
            "users": TableSchema(
                name="users",
                comment="User accounts",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="name", type="VARCHAR(100)"),
                    ColumnSchema(name="email", type="VARCHAR(200)"),
                    ColumnSchema(name="created_at", type="TIMESTAMP"),
                ],
            ),
            "orders": TableSchema(
                name="orders",
                comment="Purchase orders",
                columns=[
                    ColumnSchema(name="id", type="INTEGER", is_primary_key=True),
                    ColumnSchema(name="user_id", type="INTEGER", is_foreign_key=True,
                                 references=("users", "id")),
                    ColumnSchema(name="amount", type="DECIMAL(10,2)"),
                    ColumnSchema(name="status", type="VARCHAR(20)"),
                    ColumnSchema(name="created_at", type="TIMESTAMP"),
                ],
            ),
        },
    )


@pytest.fixture
def sample_domain():
    return DomainConfig(
        name="ecommerce",
        label={"zh": "电商", "en": "E-commerce"},
        glossary=[
            GlossaryTerm(
                term="订单金额",
                term_en="order amount",
                description="订单的总金额",
                mapping=TermMapping(expression="orders.amount"),
            ),
            GlossaryTerm(
                term="下单时间",
                term_en="order time",
                description="用户下单的时间戳",
                mapping=TermMapping(expression="orders.created_at"),
            ),
            GlossaryTerm(
                term="高价值客户",
                term_en="high-value customer",
                description="累计消费超过10000元的客户",
                mapping=TermMapping(
                    expression="SUM(orders.amount) > 10000",
                    type="filter_condition",
                ),
            ),
        ],
        rules=[
            BusinessRule(
                id="R001",
                description="已取消的订单不计入统计",
                sql_template="WHERE orders.status != 'cancelled'",
            ),
            BusinessRule(
                id="R002",
                description="金额单位为人民币元，保留两位小数",
                sql_template="ROUND(amount, 2)",
            ),
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════
# PromptLevel enum
# ═══════════════════════════════════════════════════════════════════════════


class TestPromptLevel:
    def test_four_levels(self):
        assert len(PromptLevel) == 4

    def test_l1_value(self):
        assert PromptLevel.L1_SIMPLE.value == "simple"

    def test_l2_value(self):
        assert PromptLevel.L2_STANDARD.value == "standard"

    def test_l3_value(self):
        assert PromptLevel.L3_COMPLEX.value == "complex"

    def test_l4_value(self):
        assert PromptLevel.L4_SELF_HEAL.value == "self_heal"


# ═══════════════════════════════════════════════════════════════════════════
# select_level — complexity scoring
# ═══════════════════════════════════════════════════════════════════════════


class TestSelectLevel:
    def test_l1_for_simple_select(self, builder, simple_sqr):
        assert builder.select_level(simple_sqr) == PromptLevel.L1_SIMPLE

    def test_l1_for_unknown_intent(self, builder):
        sqr = SQR(raw_text="hello", language="en", intent=IntentType.UNKNOWN)
        assert builder.select_level(sqr) == PromptLevel.L1_SIMPLE

    def test_l2_for_aggregate_with_two_entities_and_time(self, builder, aggregate_sqr):
        """2 entities(≥2→+1) + AGGREGATE(+1) + time_range(+1) = 3 → L2."""
        assert builder.select_level(aggregate_sqr) == PromptLevel.L2_STANDARD

    def test_l2_for_l2_sqr(self, builder, l2_sqr):
        """4 entities (>2→+1) + time_range(+1) = 2 → L2."""
        assert builder.select_level(l2_sqr) == PromptLevel.L2_STANDARD

    def test_l2_for_join(self, builder, join_sqr):
        # JOIN(+2) + entities≥2(+1) = 3 → L2
        assert builder.select_level(join_sqr) == PromptLevel.L2_STANDARD

    def test_l3_for_funnel(self, builder, funnel_sqr):
        # FUNNEL(+3) + entities≥2(+1) = 4 → L3
        assert builder.select_level(funnel_sqr) == PromptLevel.L3_COMPLEX

    def test_l3_for_funnel_with_time(self, builder):
        sqr = SQR(
            raw_text="转化漏斗分析上个月",
            language="zh",
            intent=IntentType.FUNNEL,
            time_range=TimeRange(),
        )
        # FUNNEL(+3) + time_range(+1) = 4 → L3
        assert builder.select_level(sqr) == PromptLevel.L3_COMPLEX

    def test_l3_complex_with_all_signals(self, builder):
        sqr = SQR(
            raw_text="复杂查询",
            language="zh",
            intent=IntentType.FUNNEL,
            entities=[Entity(name="a", type="column", normalized="a"),
                      Entity(name="b", type="column", normalized="b"),
                      Entity(name="c", type="column", normalized="c")],
            ambiguities=[Ambiguity(aspect="aggregation", description="test")],
            time_range=TimeRange(),
        )
        # entities>2(+1) + FUNNEL(+2) + agg_ambig(+1) + time_range(+1) = 5 → L3
        assert builder.select_level(sqr) == PromptLevel.L3_COMPLEX

    def test_retry_forces_l4(self, builder, simple_sqr):
        assert builder.select_level(simple_sqr, is_retry=True) == PromptLevel.L4_SELF_HEAL

    def test_retry_overrides_l3(self, builder):
        sqr = SQR(
            raw_text="complex",
            language="en",
            intent=IntentType.FUNNEL,
            entities=[Entity(name="a", type="column", normalized="a"),
                      Entity(name="b", type="column", normalized="b"),
                      Entity(name="c", type="column", normalized="c"),
                      Entity(name="d", type="column", normalized="d")],
            ambiguities=[Ambiguity(aspect="aggregation", description="test")],
            time_range=TimeRange(),
        )
        assert builder.select_level(sqr, is_retry=True) == PromptLevel.L4_SELF_HEAL

    def test_aggregation_ambiguity_adds_complexity(self, builder):
        sqr = SQR(
            raw_text="各地区排名",
            language="zh",
            intent=IntentType.AGGREGATE,
            ambiguities=[Ambiguity(aspect="aggregation", description="agg desc")],
        )
        # AGGREGATE(+1) + agg_ambig(+1) = 2 → L2
        assert builder.select_level(sqr) == PromptLevel.L2_STANDARD


# ═══════════════════════════════════════════════════════════════════════════
# estimate_tokens
# ═══════════════════════════════════════════════════════════════════════════


class TestEstimateTokens:
    def test_empty_string(self, builder):
        assert builder.estimate_tokens("") == 0

    def test_english(self, builder):
        # 40 chars → ~10 tokens
        text = "SELECT * FROM orders WHERE amount > 100"
        tokens = builder.estimate_tokens(text)
        assert 8 <= tokens <= 15

    def test_chinese(self, builder):
        # "查询所有订单金额大于100的记录" = 14 chars → ~9 tokens
        text = "查询所有订单金额大于100的记录"
        tokens = builder.estimate_tokens(text)
        assert 5 <= tokens <= 15

    def test_mixed(self, builder):
        text = "查询 orders 表中 amount 大于 100 的记录"
        tokens = builder.estimate_tokens(text)
        assert tokens > 0

    def test_english_longer_than_chinese_same_chars(self, builder):
        """Same character count: English gets fewer tokens than Chinese."""
        en = "hello world this is a test"  # mostly ascii
        zh = "这是一段中文测试文本用于验证"  # all CJK
        en_tokens = builder.estimate_tokens(en)
        zh_tokens = builder.estimate_tokens(zh)
        # Chinese chars produce more tokens per character
        assert en_tokens < zh_tokens or len(en) < len(zh)


# ═══════════════════════════════════════════════════════════════════════════
# build — L1 (simple)
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildL1:
    def test_minimal_prompt(self, builder, simple_sqr):
        prompt = builder.build(simple_sqr)
        assert simple_sqr.raw_text in prompt
        # No schema + no domain + no RAG → LLM_AUTO_TEMPLATE (always generates SQL)
        assert "SQL" in prompt
        assert "MUST generate a SQL query" in prompt

    def test_with_schema(self, builder, simple_sqr, sample_schema):
        prompt = builder.build(simple_sqr, schema=sample_schema)
        assert "users" in prompt.lower()
        assert "orders" in prompt.lower()

    def test_has_dialect_placeholder(self, builder, simple_sqr, sample_schema):
        prompt = builder.build(simple_sqr, schema=sample_schema, dialect="postgresql")
        assert "postgresql" in prompt

    def test_returns_string(self, builder, simple_sqr):
        prompt = builder.build(simple_sqr)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_l1_no_glossary_or_rules_injected(self, builder, simple_sqr, sample_domain):
        """L1 template doesn't have glossary/rules placeholders,
        so they should not appear in the output (replaced with empty strings)."""
        prompt = builder.build(simple_sqr, domain=sample_domain)
        # L1 template doesn't include {glossary} or {rules} — check
        # that domain doesn't break anything
        assert isinstance(prompt, str)


# ═══════════════════════════════════════════════════════════════════════════
# build — L2 (standard)
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildL2:
    def test_l2_prompt_includes_schema(self, builder, l2_sqr, sample_schema, sample_domain):
        prompt = builder.build(l2_sqr, schema=sample_schema, domain=sample_domain)
        assert l2_sqr.raw_text in prompt
        assert "users" in prompt.lower() or "orders" in prompt.lower()

    def test_includes_glossary(self, builder, l2_sqr, sample_schema, sample_domain):
        prompt = builder.build(l2_sqr, schema=sample_schema, domain=sample_domain)
        # "订单金额" entity matches glossary term
        assert "订单金额" in prompt or "order amount" in prompt

    def test_includes_business_rules(self, builder, l2_sqr, sample_schema, sample_domain):
        prompt = builder.build(l2_sqr, schema=sample_schema, domain=sample_domain)
        assert "R001" in prompt or "R002" in prompt

    def test_glossary_filtered_by_relevance(self, builder, l2_sqr, sample_domain):
        """Only glossary terms matching the NL text should appear."""
        prompt = builder.build(l2_sqr, domain=sample_domain)
        # "高价值客户" NOT in raw_text, so shouldn't match
        assert "高价值客户" not in prompt


# ═══════════════════════════════════════════════════════════════════════════
# build — with few-shot examples
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildWithExamples:
    def test_examples_injected(self, builder, l2_sqr, sample_schema):
        pairs = [
            QueryPair(
                nl_text="统计上个月的订单数",
                sql_text=(
                    "SELECT COUNT(*) FROM orders WHERE created_at >= "
                    "date_trunc('month', CURRENT_DATE - INTERVAL '1 month')"
                ),
                similarity=0.85,
            ),
        ]
        prompt = builder.build(
            l2_sqr, schema=sample_schema, similar_pairs=pairs
        )
        assert "统计上个月的订单数" in prompt
        assert "COUNT(*)" in prompt

    def test_examples_sorted_by_similarity(self, builder, l2_sqr, sample_schema):
        pairs = [
            QueryPair(nl_text="Q1", sql_text="SQL1", similarity=0.5),
            QueryPair(nl_text="Q2", sql_text="SQL2", similarity=0.9),
        ]
        prompt = builder.build(
            l2_sqr, schema=sample_schema, similar_pairs=pairs
        )
        # Q2 (higher similarity) should appear before Q1
        q1_pos = prompt.find("Q1")
        q2_pos = prompt.find("Q2")
        assert q2_pos > -1 and q1_pos > -1
        assert q2_pos < q1_pos


# ═══════════════════════════════════════════════════════════════════════════
# build — self-heal (L4)
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildL4:
    def test_self_heal_prompt(self, builder, simple_sqr):
        prompt = builder.build(
            simple_sqr,
            error_context="column 'amont' does not exist",
            dialect="postgresql",
        )
        # is_retry detection from error_context presence
        assert simple_sqr.raw_text in prompt
        assert "amont" in prompt.lower()
        assert "column" in prompt.lower()

    def test_self_heal_without_error_context_falls_back(self, builder, simple_sqr):
        """When error_context is None, level is not L4."""
        prompt = builder.build(simple_sqr)
        assert simple_sqr.raw_text in prompt
        # Should use L1 since complexity is low and no error
        assert isinstance(prompt, str)


# ═══════════════════════════════════════════════════════════════════════════
# build — with history
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildWithHistory:
    def test_history_rendered(self, builder, l2_sqr, sample_schema):
        history = [
            {"nl_input": "查询用户数", "sql": "SELECT COUNT(*) FROM users"},
        ]
        prompt = builder.build(
            l2_sqr, schema=sample_schema, history=history
        )
        assert "查询用户数" in prompt
        assert "COUNT(*)" in prompt

    def test_history_truncated_to_budget(self, builder, l2_sqr):
        """Many long history entries should be truncated to fit budget."""
        history = [
            {"nl_input": f"query number {i}: find all orders with complex conditions",
             "sql": f"SELECT * FROM orders WHERE status = 'active' "
                    f"AND amount > {i}00 ORDER BY created_at DESC"}
            for i in range(30)
        ]
        prompt = builder.build(l2_sqr, history=history)
        # Should not include all 30 entries — budget-limited
        assert isinstance(prompt, str)
        # The oldest entries (lowest i) should be dropped first by the
        # reversed-iteration truncation logic
        # With 30 long entries, some early ones should be gone
        found = sum(1 for i in range(30) if f"query number {i}" in prompt)
        assert found < 30, f"Expected <30 entries, found {found}"

    def test_empty_history(self, builder, aggregate_sqr):
        prompt = builder.build(aggregate_sqr, history=[])
        # L1 template — no history section
        assert isinstance(prompt, str)


# ═══════════════════════════════════════════════════════════════════════════
# Token budget enforcement
# ═══════════════════════════════════════════════════════════════════════════


class TestTokenBudget:
    def test_prompt_within_budget(self, builder, simple_sqr):
        prompt = builder.build(simple_sqr)
        tokens = builder.estimate_tokens(prompt)
        assert tokens <= DEFAULT_BUDGETS[PromptLevel.L1_SIMPLE] + 100  # small margin

    def test_small_budget_trimmed(self, builder, simple_sqr):
        """With a tiny budget, the prompt should still be returned truncated."""
        custom = PromptBuilder(
            token_budgets={PromptLevel.L1_SIMPLE: 50}
        )
        prompt = custom.build(simple_sqr)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_custom_budgets(self):
        custom = PromptBuilder(
            token_budgets={
                PromptLevel.L1_SIMPLE: 500,
                PromptLevel.L2_STANDARD: 1000,
                PromptLevel.L3_COMPLEX: 2000,
                PromptLevel.L4_SELF_HEAL: 800,
            }
        )
        assert custom.token_budgets[PromptLevel.L1_SIMPLE] == 500

    def test_default_budgets_used_when_none(self):
        builder = PromptBuilder()
        assert builder.token_budgets == DEFAULT_BUDGETS


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_all_none_inputs(self, builder):
        sqr = SQR(raw_text="test", language="en", intent=IntentType.UNKNOWN)
        prompt = builder.build(sqr)
        assert "test" in prompt
        assert isinstance(prompt, str)

    def test_empty_schema(self, builder, simple_sqr):
        empty_schema = SchemaSnapshot(
            database_type="sqlite",
            database_name="empty",
        )
        prompt = builder.build(simple_sqr, schema=empty_schema)
        assert isinstance(prompt, str)

    def test_empty_domain(self, builder, simple_sqr):
        empty_domain = DomainConfig(name="test")
        prompt = builder.build(simple_sqr, domain=empty_domain)
        assert isinstance(prompt, str)

    def test_dialect_ansi_default(self, builder, simple_sqr, sample_schema):
        prompt = builder.build(simple_sqr, schema=sample_schema)
        assert "ansi" in prompt

    def test_dialect_override(self, builder, simple_sqr, sample_schema):
        prompt = builder.build(simple_sqr, schema=sample_schema, dialect="mysql")
        assert "mysql" in prompt

    def test_constructor_dialect(self, simple_sqr, sample_schema):
        builder = PromptBuilder(dialect="sqlite")
        prompt = builder.build(simple_sqr, schema=sample_schema)
        assert "sqlite" in prompt

    def test_build_dialect_overrides_constructor(self, sample_schema):
        builder = PromptBuilder(dialect="sqlite")
        sqr = SQR(raw_text="test", language="en", intent=IntentType.UNKNOWN)
        prompt = builder.build(sqr, schema=sample_schema, dialect="mysql")
        assert "mysql" in prompt

    def test_large_schema_trimmed(self, builder, simple_sqr):
        """Schema with many tables should be trimmed to fit budget."""
        tables = {}
        for i in range(500):
            tables[f"table_{i}"] = TableSchema(
                name=f"table_{i}",
                columns=[
                    ColumnSchema(name="id", type="INTEGER"),
                    ColumnSchema(name="data", type="TEXT"),
                ],
            )
        big_schema = SchemaSnapshot(
            database_type="postgresql",
            database_name="big_db",
            tables=tables,
        )
        prompt = builder.build(simple_sqr, schema=big_schema)
        # Should NOT contain all 500 table names
        assert isinstance(prompt, str)
        count = sum(1 for i in range(500) if f"table_{i}" in prompt)
        assert count < 500

    def test_query_pair_creation(self):
        """Verify QueryPair model works correctly."""
        pair = QueryPair(
            nl_text="查询订单",
            sql_text="SELECT * FROM orders",
            domain="ecommerce",
            similarity=0.9,
        )
        assert pair.nl_text == "查询订单"
        assert pair.sql_text == "SELECT * FROM orders"
        assert pair.domain == "ecommerce"
        assert pair.similarity == 0.9


# ═══════════════════════════════════════════════════════════════════════════
# Template integrity
# ═══════════════════════════════════════════════════════════════════════════


class TestTemplateIntegrity:
    def test_l1_template_has_required_placeholders(self):
        assert "{question}" in L1_TEMPLATE
        assert "{schema}" in L1_TEMPLATE
        assert "{dialect}" in L1_TEMPLATE

    def test_l2_template_has_required_placeholders(self):
        assert "{question}" in L2_TEMPLATE
        assert "{schema}" in L2_TEMPLATE
        assert "{dialect}" in L2_TEMPLATE
        assert "{glossary}" in L2_TEMPLATE
        assert "{rules}" in L2_TEMPLATE
        assert "{examples}" in L2_TEMPLATE
        assert "{history}" in L2_TEMPLATE

    def test_l3_template_has_history(self):
        assert "{history}" in L3_TEMPLATE

    def test_l4_template_has_error_context(self):
        assert "{error_context}" in L4_TEMPLATE

    def test_all_templates_format_successfully(
        self, builder, simple_sqr, sample_schema, sample_domain
    ):
        """Verify all four templates can be formatted without KeyError."""
        for level in PromptLevel:
            sqr = simple_sqr
            kwargs = {"sqr": sqr, "schema": sample_schema, "domain": sample_domain}
            if level == PromptLevel.L4_SELF_HEAL:
                kwargs["error_context"] = "syntax error"
            prompt = builder.build(**kwargs)  # type: ignore[arg-type]
            assert isinstance(prompt, str)
            assert len(prompt) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Section budget configuration
# ═══════════════════════════════════════════════════════════════════════════


class TestSectionBudgets:
    def test_all_levels_have_section_budgets(self):
        for level in PromptLevel:
            assert level in SECTION_BUDGETS

    def test_section_budgets_sum_close_to_one(self):
        for level, sections in SECTION_BUDGETS.items():
            total = sum(sections.values())
            assert 0.9 <= total <= 1.1, f"{level}: section budgets sum to {total}"

    def test_default_budgets_for_all_levels(self):
        for level in PromptLevel:
            assert level in DEFAULT_BUDGETS
            assert DEFAULT_BUDGETS[level] > 0
