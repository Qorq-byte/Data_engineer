"""End-to-end tests for the continuous learning loop.

Tests: Feedback → Store → Analyze → Extract cycle.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.learning.feedback_collector import FeedbackCollector
from app.learning.pattern_analyzer import PatternAnalyzer, _parse_diff_operations


@pytest.fixture
def collector():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = FeedbackCollector(db_path=path)
    yield c
    c.close()
    os.unlink(path)


class TestLearningLoop:
    """Verify the full feedback → analyze → extract cycle."""

    def test_full_cycle(self, collector):
        """Feedback collection → clustering → candidate extraction."""
        # Phase 1: Collect feedback with repeated edit patterns
        for i in range(4):
            collector.collect(
                session_id=f"s{i}",
                query_id=f"q{i}",
                nl_input=f"query about orders {i}",
                sql_generated="SELECT * FROM orders",
                sql_final="SELECT * FROM orders WHERE status = 'active'",
                rating=4,
                domain_id="ecommerce",
            )

        # Verify collection
        assert collector.count() == 4
        stats = collector.get_stats(domain="ecommerce")
        assert stats["total"] == 4

        # Phase 2: Analyze patterns
        analyzer = PatternAnalyzer(collector)
        clusters = analyzer.cluster_edits(domain="ecommerce")
        assert len(clusters) >= 1  # Should find at least one cluster

        # Phase 3: Extract candidates
        candidates = analyzer.analyze(domain="ecommerce", min_occurrence=3)
        assert len(candidates) >= 1
        assert candidates[0].occurrence_count >= 3
        assert candidates[0].confidence > 0

    def test_empty_feedback_no_candidates(self, collector):
        analyzer = PatternAnalyzer(collector)
        candidates = analyzer.analyze()
        assert candidates == []

    def test_parse_diff_for_learning(self):
        """Verify diff parsing feeds correctly into learning pipeline."""
        ops = _parse_diff_operations(
            "SELECT price FROM orders",
            "SELECT SUM(price * quantity) FROM orders WHERE status != 'cancelled'",
        )
        # Should detect the changes
        assert len(ops) >= 1
        keywords = {o.get("keyword") for o in ops}
        assert len(keywords) >= 1
