"""Regression tests for continuous-learning API response types."""

from __future__ import annotations

import pytest

from app.api import learning


@pytest.mark.asyncio
async def test_learning_stats_normalizes_string_average_rating(monkeypatch: pytest.MonkeyPatch) -> None:
    """The workspace must always receive a number it can format with toFixed."""
    monkeypatch.setattr(learning, "_sync_with_feedback_store", lambda: None)
    monkeypatch.setattr(learning, "_get_fresh_candidates", lambda: [])
    monkeypatch.setitem(learning._learning_store, "total_queries", 1)
    monkeypatch.setitem(learning._learning_store, "total_feedback", 2)
    monkeypatch.setitem(learning._learning_store, "avg_rating", "3.00")

    stats = await learning.learning_stats()

    assert stats["avg_rating"] == 3.0
    assert isinstance(stats["avg_rating"], float)
