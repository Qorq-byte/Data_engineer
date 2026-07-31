"""Tests for PatternAnalyzer — diff parsing, clustering, rule extraction."""

from __future__ import annotations

import os
import tempfile

import pytest

from app.learning.feedback_collector import FeedbackCollector
from app.learning.pattern_analyzer import (
    CandidateRule,
    PatternAnalyzer,
    _normalize_operation,
    _parse_diff_operations,
)


@pytest.fixture
def collector():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = FeedbackCollector(db_path=path)
    yield c
    c.close()
    os.unlink(path)


@pytest.fixture
def analyzer(collector):
    return PatternAnalyzer(collector)


# ── _parse_diff_operations ────────────────────────────────────────────────


class TestParseDiffOperations:
    def test_add_where_clause(self):
        ops = _parse_diff_operations(
            "SELECT * FROM orders",
            "SELECT * FROM orders WHERE status = 'completed'",
        )
        assert len(ops) >= 1
        where_ops = [o for o in ops if o.get("keyword") == "WHERE"]
        assert len(where_ops) >= 1

    def test_add_and_condition(self):
        ops = _parse_diff_operations(
            "SELECT * FROM orders WHERE region = 'East'",
            "SELECT * FROM orders WHERE region = 'East' AND status = 'active'",
        )
        and_ops = [o for o in ops if o.get("keyword") == "AND"]
        assert len(and_ops) >= 1

    def test_remove_condition(self):
        ops = _parse_diff_operations(
            "SELECT * FROM orders WHERE status = 'cancelled'",
            "SELECT * FROM orders",
        )
        assert len(ops) >= 1
        assert ops[0]["type"] == "remove"

    def test_aggregate_change(self):
        ops = _parse_diff_operations(
            "SELECT price FROM orders",
            "SELECT SUM(price) FROM orders",
        )
        agg_ops = [o for o in ops if o.get("keyword") == "AGGREGATE"]
        assert len(agg_ops) >= 1

    def test_identical_sql_returns_empty(self):
        assert _parse_diff_operations("SELECT 1", "SELECT 1") == []

    def test_empty_inputs(self):
        assert _parse_diff_operations("", "SELECT 1") == []

    def test_extracts_column(self):
        ops = _parse_diff_operations(
            "SELECT * FROM orders",
            "SELECT * FROM orders WHERE o.amount > 100",
        )
        col_ops = [o for o in ops if o.get("column") == "amount"]
        assert len(col_ops) >= 1


class TestNormalizeOperation:
    def test_add_where(self):
        op = {"type": "add", "keyword": "WHERE", "table": "orders", "column": "status"}
        assert _normalize_operation(op) == "ADD_WHERE_ORDERS_STATUS"

    def test_remove_and(self):
        op = {"type": "remove", "keyword": "AND", "table": "", "column": "region"}
        key = _normalize_operation(op)
        assert "REMOVE" in key
        assert "AND" in key
        assert "REGION" in key

    def test_aggregate(self):
        op = {"type": "add", "keyword": "AGGREGATE", "table": "orders", "column": "amount"}
        assert "AGGREGATE" in _normalize_operation(op)


# ── cluster_edits ─────────────────────────────────────────────────────────


class TestClusterEdits:
    def test_clusters_similar_edits(self, analyzer, collector):
        # Add 3 feedback records with the same edit pattern
        for i in range(3):
            collector.collect(
                session_id=f"s{i}",
                query_id=f"q{i}",
                nl_input=f"query {i}",
                sql_generated="SELECT * FROM orders",
                sql_final="SELECT * FROM orders WHERE status = 'completed'",
                domain_id="ecommerce",
            )
        clusters = analyzer.cluster_edits(domain="ecommerce")
        assert len(clusters) >= 1

    def test_ignores_no_edit(self, analyzer, collector):
        collector.collect(
            session_id="s1", query_id="q1",
            nl_input="q", sql_generated="SELECT 1", sql_final="SELECT 1",
        )
        clusters = analyzer.cluster_edits()
        assert all(len(v) == 0 for v in clusters.values()) or len(clusters) == 0

    def test_empty_no_feedback(self, analyzer):
        clusters = analyzer.cluster_edits()
        assert isinstance(clusters, dict)


# ── rank_candidates ───────────────────────────────────────────────────────


class TestRankCandidates:
    def test_ranks_by_frequency(self, analyzer):
        clusters = {
            "ADD_WHERE_ORDERS_STATUS": [
                {
                    "feedback_id": "f1", "query_id": "q1",
                    "op": {"type": "add", "keyword": "WHERE",
                           "table": "orders", "column": "status"},
                    "domain_id": "ecom",
                },
                {
                    "feedback_id": "f2", "query_id": "q2",
                    "op": {"type": "add", "keyword": "WHERE",
                           "table": "orders", "column": "status"},
                    "domain_id": "ecom",
                },
                {
                    "feedback_id": "f3", "query_id": "q3",
                    "op": {"type": "add", "keyword": "WHERE",
                           "table": "orders", "column": "status"},
                    "domain_id": "ecom",
                },
            ],
            "ADD_WHERE_USERS_ACTIVE": [
                {
                    "feedback_id": "f4", "query_id": "q4",
                    "op": {"type": "add", "keyword": "WHERE",
                           "table": "users", "column": "active"},
                    "domain_id": "ecom",
                },
                {
                    "feedback_id": "f5", "query_id": "q5",
                    "op": {"type": "add", "keyword": "WHERE",
                           "table": "users", "column": "active"},
                    "domain_id": "ecom",
                },
            ],
        }
        candidates = analyzer.rank_candidates(clusters)
        # The cluster with 3 items should rank higher and be first
        assert len(candidates) == 2
        assert candidates[0].occurrence_count == 3

    def test_minimum_size_two(self, analyzer):
        """Clusters with fewer than 2 items are skipped."""
        clusters = {
            "ADD_WHERE_X_Y": [
                {
                    "feedback_id": "f1", "query_id": "q1",
                    "op": {"type": "add", "keyword": "WHERE",
                           "table": "x", "column": "y"},
                    "domain_id": "e",
                },
            ],
        }
        candidates = analyzer.rank_candidates(clusters)
        assert len(candidates) == 0

    def test_empty_clusters(self, analyzer):
        assert analyzer.rank_candidates({}) == []


# ── analyze (full pipeline) ───────────────────────────────────────────────


class TestAnalyze:
    def test_full_pipeline(self, analyzer, collector):
        # Insert feedback with repeated patterns
        for i in range(4):
            collector.collect(
                session_id=f"s{i}",
                query_id=f"q{i}",
                nl_input=f"query {i}",
                sql_generated="SELECT * FROM orders",
                sql_final="SELECT * FROM orders WHERE status != 'cancelled'",
                domain_id="ecommerce",
            )
        candidates = analyzer.analyze(domain="ecommerce", min_occurrence=3)
        assert len(candidates) >= 1
        assert all(c.occurrence_count >= 3 for c in candidates)

    def test_min_occurrence_filters(self, analyzer, collector):
        for i in range(2):
            collector.collect(
                session_id=f"s{i}", query_id=f"q{i}",
                nl_input=f"q{i}", sql_generated="SELECT * FROM t",
                sql_final="SELECT * FROM t WHERE x=1",
            )
        candidates = analyzer.analyze(min_occurrence=3)
        assert len(candidates) == 0  # only 2, below threshold

    def test_empty_feedback_returns_empty(self, analyzer):
        assert analyzer.analyze() == []


# ── CandidateRule dataclass ───────────────────────────────────────────────


class TestCandidateRule:
    def test_defaults(self):
        cr = CandidateRule()
        assert cr.rule_id.startswith("cr_")
        assert cr.status == "pending_review"
        assert cr.confidence == 0.0
        assert cr.supporting_query_ids == []

    def test_full_creation(self):
        cr = CandidateRule(
            rule_id="cr_test",
            domain_id="ecom",
            pattern_description="添加 WHERE status",
            occurrence_count=5,
            confidence=0.85,
            status="approved",
            supporting_query_ids=["f1", "f2"],
        )
        assert cr.rule_id == "cr_test"
        assert cr.occurrence_count == 5
        assert cr.status == "approved"


# ── serialize / deserialize ───────────────────────────────────────────────


class TestSerialize:
    def test_to_dicts_roundtrip(self, analyzer):
        original = [
            CandidateRule(
                rule_id="cr_001", domain_id="ecom",
                pattern_description="test", occurrence_count=3,
                confidence=0.8, supporting_query_ids=["a", "b"],
            ),
        ]
        data = analyzer.to_dicts(original)
        restored = PatternAnalyzer.from_dicts(data)
        assert len(restored) == 1
        assert restored[0].rule_id == "cr_001"
        assert restored[0].occurrence_count == 3
        assert restored[0].supporting_query_ids == ["a", "b"]
