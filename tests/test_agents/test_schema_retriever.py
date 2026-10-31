"""Tests for SchemaRetrievalAgent — wraps SchemaRetriever + Glossary + Rules."""

from __future__ import annotations

import pytest

from app.agents.base import AgentStatus
from app.agents.schema_retriever import SchemaRetrievalAgent


class TestSchemaRetrievalAgent:
    @pytest.fixture
    def agent(self):
        return SchemaRetrievalAgent()

    @pytest.mark.anyio
    async def test_name_and_description(self, agent):
        assert agent.name == "schema_retriever"
        assert agent.description

    @pytest.mark.anyio
    async def test_execute_with_sqr(self, agent):
        from app.models.query import SQR
        sqr = SQR(raw_text="find all users", language="en")
        result = await agent.execute(sqr, {})
        assert result.status == AgentStatus.DONE
        assert isinstance(result.data, dict)
        assert "tables" in result.data

    @pytest.mark.anyio
    async def test_execute_with_string_input(self, agent):
        result = await agent.execute("find orders", {})
        assert result.status == AgentStatus.DONE
        assert isinstance(result.data, dict)

    @pytest.mark.anyio
    async def test_execute_with_dict_input(self, agent):
        result = await agent.execute({"nl_text": "统计产品销量"}, {})
        assert result.status == AgentStatus.DONE
        assert isinstance(result.data, dict)

    @pytest.mark.anyio
    async def test_execute_with_dict_containing_sqr(self, agent):
        from app.models.query import SQR
        sqr = SQR(raw_text="query", language="zh")
        result = await agent.execute({"sqr": sqr}, {})
        assert result.status == AgentStatus.DONE
        assert isinstance(result.data, dict)

    @pytest.mark.anyio
    async def test_no_retriever_returns_empty_tables(self, agent):
        result = await agent.execute("some query", {})
        assert result.data["tables"] == []
        assert result.data["terms"] == []
        assert result.data["rules"] == []

    @pytest.mark.anyio
    async def test_metadata_includes_counts(self, agent):
        result = await agent.execute("test", {})
        assert "table_count" in result.metadata
        assert "term_count" in result.metadata
        assert "rule_count" in result.metadata

    @pytest.mark.anyio
    async def test_metadata_includes_latency(self, agent):
        result = await agent.execute("test", {})
        assert "latency_ms" in result.metadata
        assert result.metadata["latency_ms"] >= 0

    @pytest.mark.anyio
    async def test_with_schema_in_context(self, agent):
        from app.models.schema import SchemaSnapshot
        schema = SchemaSnapshot(database_type="sqlite", database_name="test", tables={})
        result = await agent.execute("query", {"schema": schema})
        assert result.status == AgentStatus.DONE
        assert result.data["filtered_schema"] is None  # no retriever, so None

    @pytest.mark.anyio
    async def test_reset(self, agent):
        await agent.execute("test", {})
        agent.reset()
        assert agent.status == AgentStatus.IDLE


class _BrokenMatcher:
    """Stands in for a glossary or rule engine that raises during matching."""

    def match(self, *args, **kwargs):
        raise RuntimeError("matcher exploded")


class TestGlossaryAndRuleIntegration:
    """The agent used to `await` two synchronous matchers and pass a keyword they
    do not accept, so `contextlib.suppress` turned both failures into permanently
    empty lists. These tests fail if that regression comes back."""

    @staticmethod
    def _glossary_with_term(term_name="销量", domain="ecommerce"):
        from app.knowledge.glossary import GlossaryManager
        from app.models.domain import GlossaryTerm

        glossary = GlossaryManager()
        glossary.add(GlossaryTerm(term=term_name, term_en="sales"), domain=domain)
        return glossary

    @staticmethod
    def _rules_with_pattern(rule_id="r_sales", pattern="销量", domain="ecommerce"):
        from app.knowledge.rule_engine import RuleEngine
        from app.models.domain import BusinessRule

        rules = RuleEngine()
        rule = BusinessRule(id=rule_id, description="sales rule", pattern=pattern)
        rules.add(rule, domain=domain)
        return rules

    @pytest.mark.anyio
    async def test_glossary_terms_reach_the_result(self):
        agent = SchemaRetrievalAgent(glossary=self._glossary_with_term())
        result = await agent.execute("统计销量", {"domain_id": "ecommerce"})
        assert result.status == AgentStatus.DONE
        assert result.metadata["term_match_error"] is None
        assert result.metadata["term_count"] == 1
        term_name, term, score = result.data["terms"][0]
        assert (term_name, term.term) == ("销量", "销量")
        assert 0.0 < score <= 1.0

    @pytest.mark.anyio
    async def test_rules_reach_the_result(self):
        agent = SchemaRetrievalAgent(rules=self._rules_with_pattern())
        result = await agent.execute("统计销量", {"domain_id": "ecommerce"})
        assert result.status == AgentStatus.DONE
        assert result.metadata["rule_match_error"] is None
        assert result.metadata["rule_count"] == 1
        match = result.data["rules"][0]
        assert match.rule.id == "r_sales"
        assert match.score > 0.0

    @pytest.mark.anyio
    async def test_domain_id_is_actually_forwarded(self):
        """A domain-scoped term must not leak into another domain's lookup; this
        only holds if `domain_id` reaches `match(domain=...)`."""
        agent = SchemaRetrievalAgent(
            glossary=self._glossary_with_term(domain="ecommerce"),
            rules=self._rules_with_pattern(domain="ecommerce"),
        )
        other = await agent.execute("统计销量", {"domain_id": "finance"})
        assert other.data["terms"] == []
        assert other.data["rules"] == []

        same = await agent.execute("统计销量", {"domain_id": "ecommerce"})
        assert len(same.data["terms"]) == 1
        assert len(same.data["rules"]) == 1

    @pytest.mark.anyio
    async def test_global_terms_match_without_a_domain(self):
        from app.knowledge.glossary import GlossaryManager
        from app.models.domain import GlossaryTerm

        glossary = GlossaryManager()
        glossary.add(GlossaryTerm(term="销量"), domain=None)
        agent = SchemaRetrievalAgent(glossary=glossary)
        result = await agent.execute("统计销量", {"domain_id": ""})
        assert result.metadata["term_count"] == 1

    @pytest.mark.anyio
    async def test_matching_failure_is_reported_not_swallowed(self):
        agent = SchemaRetrievalAgent(glossary=_BrokenMatcher(), rules=_BrokenMatcher())
        result = await agent.execute("统计销量", {"domain_id": "ecommerce"})
        assert result.status == AgentStatus.DONE
        assert result.data["terms"] == []
        assert result.data["rules"] == []
        assert "matcher exploded" in result.metadata["term_match_error"]
        assert "matcher exploded" in result.metadata["rule_match_error"]
