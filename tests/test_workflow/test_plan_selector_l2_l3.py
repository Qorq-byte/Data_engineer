"""Tests for PlanSelector L2 (history matching) and L3 (LLM classification).

Covers PlanHistoryStore CRUD, normalization, Jaccard similarity, domain
filtering, L2 plan voting, L3 mock-router classification, response parsing
helpers, and L1 → L2 → L3 → default layer ordering.

Uses real SQLite (in-memory and on-disk) — no mocks.  The LLM is exercised
through ``LiteLLMRouter``'s built-in mock mode.
"""

from __future__ import annotations

import pytest

from app.harness.plan_loader import PlanLoader
from app.llm.router import LiteLLMRouter, RouterConfig
from app.workflow.history_store import (
    JACCARD_THRESHOLD,
    HistoryRecord,
    PlanHistoryStore,
    jaccard_similarity,
    normalize_query,
)
from app.workflow.plan_selector import (
    DEFAULT_PLAN,
    PlanSelector,
    _extract_response_text,
    _parse_plan_id,
)

# ── Helpers ────────────────────────────────────────────────────────────

# A query that matches no L1 rule — falls through to L2/L3/default.
NEUTRAL_QUERY = "quarterly revenue overview"


def _make_selector(
    history_store: PlanHistoryStore | None = None,
    llm_router: object | None = None,
) -> PlanSelector:
    """Create a PlanSelector with a real PlanLoader and optional L2/L3 deps."""
    loader = PlanLoader("app/workflow/plans")
    return PlanSelector(
        plan_loader=loader,
        history_store=history_store,
        llm_router=llm_router,
    )


def _mock_router(default_model: str) -> LiteLLMRouter:
    """LiteLLMRouter in mock mode.

    Mock responses embed the model name (``[mock response from mock/<model>]``),
    so setting ``default_model`` to a plan ID makes the mock LLM "answer"
    with that plan ID.
    """
    return LiteLLMRouter(RouterConfig(providers={}, default_model=default_model), mock_mode=True)


class _RaisingRouter:
    """Router stub whose complete() always raises — for L3 error handling."""

    async def complete(self, *args: object, **kwargs: object) -> dict:
        raise RuntimeError("LLM unavailable")


@pytest.fixture
def store() -> PlanHistoryStore:
    s = PlanHistoryStore(":memory:")
    yield s
    s.close()


# ═══════════════════════════════════════════════════════════════════════
# PlanHistoryStore — CRUD
# ═══════════════════════════════════════════════════════════════════════


class TestPlanHistoryStoreCRUD:
    def test_record_and_count(self, store: PlanHistoryStore):
        assert store.count() == 0
        store.record("show users", "ez_query")
        store.record("monthly sales", "gensql_agentic")
        assert store.count() == 2

    def test_clear(self, store: PlanHistoryStore):
        store.record("show users", "ez_query")
        store.clear()
        assert store.count() == 0

    def test_find_similar_on_empty_store(self, store: PlanHistoryStore):
        assert store.find_similar("anything at all") == []

    def test_record_fields_roundtrip(self, store: PlanHistoryStore):
        store.record("Show Users!", "ez_query", domain="shop", success=True)
        results = store.find_similar("show users")
        assert len(results) == 1
        rec = results[0]
        assert isinstance(rec, HistoryRecord)
        assert rec.query_text == "Show Users!"
        assert rec.plan_id == "ez_query"
        assert rec.domain == "shop"
        assert rec.success is True
        assert rec.created_at  # ISO timestamp recorded
        assert rec.similarity == 1.0

    def test_failed_run_recorded_with_success_false(self, store: PlanHistoryStore):
        store.record("show users", "ez_query", success=False)
        results = store.find_similar("show users")
        assert len(results) == 1
        assert results[0].success is False

    def test_disk_persistence(self, tmp_path):
        db_path = tmp_path / "history.db"
        s1 = PlanHistoryStore(db_path)
        s1.record("quarterly revenue overview", "ez_query")
        s1.close()

        s2 = PlanHistoryStore(db_path)
        try:
            assert s2.count() == 1
            results = s2.find_similar("quarterly revenue overview")
            assert results[0].plan_id == "ez_query"
        finally:
            s2.close()


# ═══════════════════════════════════════════════════════════════════════
# Normalization + Jaccard helpers
# ═══════════════════════════════════════════════════════════════════════


class TestNormalizationAndJaccard:
    def test_normalize_lowercase_and_punctuation(self):
        assert normalize_query("Show, Users!") == "show users"

    def test_normalize_collapses_whitespace(self):
        assert normalize_query("  show   \t users \n ") == "show users"

    def test_jaccard_identical_sets(self):
        assert jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0

    def test_jaccard_disjoint_sets(self):
        assert jaccard_similarity({"a", "b"}, {"c", "d"}) == 0.0

    def test_jaccard_empty_sets(self):
        assert jaccard_similarity(set(), {"a"}) == 0.0
        assert jaccard_similarity(set(), set()) == 0.0

    def test_jaccard_partial_overlap(self):
        # 3 shared / 5 total = 0.6 — exactly at the threshold
        sim = jaccard_similarity({"a", "b", "c", "d"}, {"a", "b", "c", "e"})
        assert sim == pytest.approx(0.6)
        assert sim >= JACCARD_THRESHOLD


# ═══════════════════════════════════════════════════════════════════════
# PlanHistoryStore — find_similar
# ═══════════════════════════════════════════════════════════════════════


class TestFindSimilar:
    def test_exact_match_ignores_case_and_punctuation(self, store: PlanHistoryStore):
        store.record("Monthly Sales Report?", "gensql_agentic")
        results = store.find_similar("monthly sales report")
        assert len(results) == 1
        assert results[0].similarity == 1.0

    def test_exact_match_ranks_before_jaccard(self, store: PlanHistoryStore):
        store.record("monthly sales report by area", "gensql_agentic")  # near match
        store.record("monthly sales report by region", "ez_query")  # exact match
        results = store.find_similar("monthly sales report by region")
        assert len(results) == 2
        assert results[0].plan_id == "ez_query"
        assert results[0].similarity == 1.0
        assert results[1].similarity < 1.0

    def test_jaccard_similar_query_matches(self, store: PlanHistoryStore):
        store.record("monthly sales report by region", "gensql_agentic")
        results = store.find_similar("monthly sales report by area")
        assert len(results) == 1
        assert results[0].plan_id == "gensql_agentic"
        assert JACCARD_THRESHOLD <= results[0].similarity < 1.0

    def test_jaccard_below_threshold_not_matched(self, store: PlanHistoryStore):
        store.record("monthly sales report", "gensql_agentic")
        assert store.find_similar("weekly revenue numbers") == []

    def test_chinese_query_tokenized_with_jieba(self, store: PlanHistoryStore):
        store.record("查看用户订单数据", "ez_query")
        # Same words, different spacing — normalized text differs,
        # but jieba token sets are identical (Jaccard 1.0).
        results = store.find_similar("查看 用户订单 数据")
        assert len(results) == 1
        assert results[0].plan_id == "ez_query"

    def test_domain_filter_excludes_other_domains(self, store: PlanHistoryStore):
        store.record("quarterly revenue overview", "ez_query", domain="ecommerce")
        assert store.find_similar("quarterly revenue overview", domain="finance") == []
        results = store.find_similar("quarterly revenue overview", domain="ecommerce")
        assert len(results) == 1

    def test_domain_none_matches_all_domains(self, store: PlanHistoryStore):
        store.record("quarterly revenue overview", "ez_query", domain="ecommerce")
        store.record("quarterly revenue overview", "ez_query", domain="finance")
        assert len(store.find_similar("quarterly revenue overview")) == 2

    def test_limit_respected(self, store: PlanHistoryStore):
        for _ in range(8):
            store.record("quarterly revenue overview", "ez_query")
        assert len(store.find_similar("quarterly revenue overview", limit=5)) == 5

    def test_empty_query_returns_nothing(self, store: PlanHistoryStore):
        store.record("quarterly revenue overview", "ez_query")
        assert store.find_similar("") == []
        assert store.find_similar("!!!") == []


# ═══════════════════════════════════════════════════════════════════════
# L2 — history-based selection
# ═══════════════════════════════════════════════════════════════════════


class TestL2HistorySelection:
    async def test_l2_no_store_returns_none(self):
        selector = _make_selector(history_store=None)
        assert await selector._select_by_history(NEUTRAL_QUERY) is None

    async def test_l2_exact_history_match(self, store: PlanHistoryStore):
        store.record(NEUTRAL_QUERY, "ez_query")
        selector = _make_selector(history_store=store)
        r = await selector.select(NEUTRAL_QUERY)
        assert r.plan_id == "ez_query"
        assert r.layer == "L2"
        assert r.confidence == 0.75

    async def test_l2_most_successful_plan_wins(self, store: PlanHistoryStore):
        for _ in range(3):
            store.record(NEUTRAL_QUERY, "ez_query")
        store.record(NEUTRAL_QUERY, "gensql_agentic")
        selector = _make_selector(history_store=store)
        r = await selector.select(NEUTRAL_QUERY)
        assert r.plan_id == "ez_query"
        assert r.layer == "L2"
        assert "3" in r.reason

    async def test_l2_ignores_failed_records(self, store: PlanHistoryStore):
        store.record(NEUTRAL_QUERY, "ez_query", success=False)
        store.record(NEUTRAL_QUERY, "ez_query", success=False)
        selector = _make_selector(history_store=store)
        r = await selector.select(NEUTRAL_QUERY)
        assert r.plan_id == DEFAULT_PLAN
        assert r.layer == "default"

    async def test_l2_filters_unavailable_plans(self, store: PlanHistoryStore):
        store.record(NEUTRAL_QUERY, "no_such_plan")
        selector = _make_selector(history_store=store)
        r = await selector.select(NEUTRAL_QUERY)
        assert r.plan_id == DEFAULT_PLAN
        assert r.layer == "default"

    async def test_l2_domain_filter_via_select(self, store: PlanHistoryStore):
        store.record(NEUTRAL_QUERY, "ez_query", domain="sales")
        selector = _make_selector(history_store=store)

        miss = await selector.select(NEUTRAL_QUERY, domain="marketing")
        assert miss.layer == "default"

        hit = await selector.select(NEUTRAL_QUERY, domain="sales")
        assert hit.layer == "L2"
        assert hit.plan_id == "ez_query"

    async def test_l2_alternatives_exclude_selected(self, store: PlanHistoryStore):
        store.record(NEUTRAL_QUERY, "ez_query")
        selector = _make_selector(history_store=store)
        r = await selector.select(NEUTRAL_QUERY)
        assert r.plan_id not in r.alternatives
        assert "gensql_agentic" in r.alternatives


# ═══════════════════════════════════════════════════════════════════════
# L3 — LLM classification
# ═══════════════════════════════════════════════════════════════════════


class TestL3LLMSelection:
    async def test_l3_no_router_returns_none(self):
        selector = _make_selector(llm_router=None)
        assert await selector._select_by_llm(NEUTRAL_QUERY) is None

    async def test_l3_mock_router_returns_valid_plan(self):
        # Mock response embeds the model name → contains "ez_query"
        selector = _make_selector(llm_router=_mock_router("ez_query"))
        r = await selector.select(NEUTRAL_QUERY)
        assert r.plan_id == "ez_query"
        assert r.layer == "L3"
        assert r.confidence == 0.6

    async def test_l3_mock_router_returns_gensql(self):
        selector = _make_selector(llm_router=_mock_router("gensql_agentic"))
        r = await selector.select(NEUTRAL_QUERY)
        assert r.plan_id == "gensql_agentic"
        assert r.layer == "L3"

    async def test_l3_irrelevant_text_falls_to_default(self):
        # Mock response mentions no plan ID → unparseable → default plan
        selector = _make_selector(llm_router=_mock_router("claude-sonnet-4"))
        r = await selector.select(NEUTRAL_QUERY)
        assert r.plan_id == DEFAULT_PLAN
        assert r.layer == "default"
        assert r.confidence == 0.3

    async def test_l3_router_exception_falls_to_default(self):
        selector = _make_selector(llm_router=_RaisingRouter())
        r = await selector.select(NEUTRAL_QUERY)
        assert r.plan_id == DEFAULT_PLAN
        assert r.layer == "default"

    async def test_l3_alternatives_exclude_selected(self):
        selector = _make_selector(llm_router=_mock_router("ez_query"))
        r = await selector.select(NEUTRAL_QUERY)
        assert r.plan_id not in r.alternatives

    def test_l3_prompt_lists_available_plans(self):
        selector = _make_selector()
        prompt = selector._build_llm_prompt(NEUTRAL_QUERY, {"ez_query", "gensql_agentic"}, "sales")
        assert "ez_query" in prompt
        assert "gensql_agentic" in prompt
        assert NEUTRAL_QUERY in prompt
        assert "sales" in prompt


# ═══════════════════════════════════════════════════════════════════════
# Response parsing helpers
# ═══════════════════════════════════════════════════════════════════════


class TestResponseParsing:
    def test_parse_plan_id_exact(self):
        assert _parse_plan_id("ez_query", {"ez_query", "gensql_agentic"}) == "ez_query"

    def test_parse_plan_id_case_insensitive_with_prose(self):
        text = "I would choose EZ_QUERY for this simple lookup."
        assert _parse_plan_id(text, {"ez_query", "gensql_agentic"}) == "ez_query"

    def test_parse_plan_id_earliest_occurrence_wins(self):
        text = "gensql_agentic (not ez_query)"
        assert _parse_plan_id(text, {"ez_query", "gensql_agentic"}) == "gensql_agentic"

    def test_parse_plan_id_no_match_returns_none(self):
        assert _parse_plan_id("no plan mentioned here", {"ez_query"}) is None

    def test_parse_plan_id_empty_text_returns_none(self):
        assert _parse_plan_id("", {"ez_query"}) is None

    def test_extract_response_text_dict_shape(self):
        response = {"choices": [{"message": {"role": "assistant", "content": "ez_query"}}]}
        assert _extract_response_text(response) == "ez_query"

    def test_extract_response_text_object_shape(self):
        class _Msg:
            content = "gensql_agentic"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        assert _extract_response_text(_Resp()) == "gensql_agentic"

    def test_extract_response_text_empty_or_none(self):
        assert _extract_response_text(None) == ""
        assert _extract_response_text({"choices": []}) == ""
        assert _extract_response_text({}) == ""


# ═══════════════════════════════════════════════════════════════════════
# Layer ordering — L1 → L2 → L3 → default
# ═══════════════════════════════════════════════════════════════════════


class TestLayerOrdering:
    async def test_l1_takes_priority_over_l2_and_l3(self, store: PlanHistoryStore):
        # History says ez_query for this exact query, and the mock LLM
        # would also answer ez_query — but the JOIN keyword triggers L1.
        query = "show me a JOIN of orders and users"
        store.record(query, "ez_query")
        selector = _make_selector(history_store=store, llm_router=_mock_router("ez_query"))
        r = await selector.select(query)
        assert r.plan_id == "gensql_agentic"
        assert r.layer == "L1"

    async def test_l2_takes_priority_over_l3(self, store: PlanHistoryStore):
        # History says ez_query; the mock LLM would say gensql_agentic.
        store.record(NEUTRAL_QUERY, "ez_query")
        selector = _make_selector(
            history_store=store, llm_router=_mock_router("gensql_agentic")
        )
        r = await selector.select(NEUTRAL_QUERY)
        assert r.plan_id == "ez_query"
        assert r.layer == "L2"

    async def test_l3_used_when_l2_has_no_match(self, store: PlanHistoryStore):
        store.record("totally unrelated historical entry", "ez_query")
        selector = _make_selector(history_store=store, llm_router=_mock_router("ez_query"))
        r = await selector.select(NEUTRAL_QUERY)
        assert r.layer == "L3"
        assert r.plan_id == "ez_query"

    async def test_default_when_all_layers_fail(self):
        selector = _make_selector(
            history_store=PlanHistoryStore(":memory:"),
            llm_router=_mock_router("claude-sonnet-4"),
        )
        r = await selector.select(NEUTRAL_QUERY)
        assert r.plan_id == DEFAULT_PLAN
        assert r.layer == "default"
        assert r.confidence == 0.3
