"""Tests for FeedbackCollector — collect, query, stats, and export."""

from __future__ import annotations

import os
import tempfile

import pytest

from app.learning.feedback_collector import FeedbackCollector
from app.models.feedback import DiffOp, FeedbackData


@pytest.fixture
def collector():
    """Create a FeedbackCollector backed by a temporary SQLite database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = FeedbackCollector(db_path=path)
    yield c
    c.close()
    os.unlink(path)


class TestCollect:
    def test_collect_returns_id(self, collector):
        fid = collector.collect(
            session_id="s1", query_id="q1",
            nl_input="订单总额", sql_generated="SELECT SUM(amount) FROM orders",
        )
        assert len(fid) == 32
        assert all(c in "0123456789abcdef" for c in fid)

    def test_collect_persists(self, collector):
        fid = collector.collect(
            session_id="s1", query_id="q1",
            nl_input="test", sql_generated="SELECT 1",
        )
        rec = collector.get_by_id(fid)
        assert rec is not None
        assert rec["nl_input"] == "test"
        assert rec["sql_generated"] == "SELECT 1"
        assert rec["session_id"] == "s1"

    def test_collect_with_all_fields(self, collector):
        fid = collector.collect(
            session_id="sess_1",
            query_id="q_1",
            nl_input="订单总额",
            sql_generated="SELECT SUM(amount) FROM orders",
            sql_final="SELECT SUM(amount) FROM orders WHERE status='completed'",
            rating=4,
            feedback_text="需要过滤已取消订单",
            diff_operations=[
                DiffOp(op_type="insert", position=40, new_text=" WHERE status='completed'")
            ],
            execution_success=True,
            execution_time_ms=230,
            user_cancelled=False,
            domain_id="ecommerce",
        )
        rec = collector.get_by_id(fid)
        assert rec is not None
        assert rec["rating"] == 4
        assert rec["feedback_text"] == "需要过滤已取消订单"
        assert rec["execution_success"] == 1
        assert rec["execution_time_ms"] == 230
        assert rec["domain_id"] == "ecommerce"
        assert rec["diff_count"] == 1

    def test_collect_rating_out_of_range_clamped(self, collector):
        fid = collector.collect(
            session_id="s1", query_id="q1",
            nl_input="test", sql_generated="SELECT 1", rating=99,
        )
        rec = collector.get_by_id(fid)
        assert rec is not None
        assert rec["rating"] == 0  # 99 is outside 1-5, clamped to 0

    def test_collect_zero_rating(self, collector):
        fid = collector.collect(
            session_id="s1", query_id="q1",
            nl_input="test", sql_generated="SELECT 1", rating=0,
        )
        rec = collector.get_by_id(fid)
        assert rec is not None
        assert rec["rating"] == 0

    def test_collect_whitespace_trimmed(self, collector):
        fid = collector.collect(
            session_id=" s1 ", query_id=" q1 ",
            nl_input="  订单总额  ", sql_generated="  SELECT 1  ",
        )
        rec = collector.get_by_id(fid)
        assert rec is not None
        assert rec["nl_input"] == "订单总额"
        assert rec["sql_generated"] == "SELECT 1"

    def test_collect_from_dataclass(self, collector):
        fb = FeedbackData(
            session_id="s1",
            query_id="q1",
            nl_input="test nl",
            sql_generated="SELECT a",
            sql_final="SELECT a, b",
            rating=3,
            feedback_text="good",
            execution_success=True,
            execution_time_ms=100,
        )
        fid = collector.collect_from_dataclass(fb)
        rec = collector.get_by_id(fid)
        assert rec is not None
        assert rec["nl_input"] == "test nl"
        assert rec["sql_final"] == "SELECT a, b"
        assert rec["rating"] == 3

    def test_collect_empty_sql_final(self, collector):
        fid = collector.collect(
            session_id="s1", query_id="q1",
            nl_input="test", sql_generated="SELECT 1", sql_final="",
        )
        rec = collector.get_by_id(fid)
        assert rec is not None
        assert rec["sql_final"] == ""


class TestGetRecent:
    def test_returns_recent(self, collector):
        for i in range(5):
            collector.collect(
                session_id=f"s{i}", query_id=f"q{i}",
                nl_input=f"query {i}", sql_generated=f"SELECT {i}",
            )
        recent = collector.get_recent(limit=3)
        assert len(recent) == 3

    def test_domain_filter(self, collector):
        collector.collect(
            session_id="s1", query_id="q1", nl_input="a",
            sql_generated="SELECT 1", domain_id="ecommerce",
        )
        collector.collect(
            session_id="s2", query_id="q2", nl_input="b",
            sql_generated="SELECT 2", domain_id="finance",
        )
        recent = collector.get_recent(limit=10, domain="ecommerce")
        assert all(r["domain_id"] == "ecommerce" for r in recent)

    def test_min_rating_filter(self, collector):
        collector.collect(
            session_id="s1", query_id="q1", nl_input="a",
            sql_generated="SELECT 1", rating=2,
        )
        collector.collect(
            session_id="s2", query_id="q2", nl_input="b",
            sql_generated="SELECT 2", rating=4,
        )
        collector.collect(
            session_id="s3", query_id="q3", nl_input="c",
            sql_generated="SELECT 3", rating=5,
        )
        recent = collector.get_recent(limit=10, min_rating=4)
        assert all(r["rating"] >= 4 for r in recent)


class TestGetById:
    def test_existing(self, collector):
        fid = collector.collect(
            session_id="s1", query_id="q1", nl_input="a", sql_generated="SELECT 1"
        )
        rec = collector.get_by_id(fid)
        assert rec is not None
        assert rec["id"] == fid

    def test_not_found(self, collector):
        assert collector.get_by_id("nonexistent") is None


class TestGetStats:
    def test_empty(self, collector):
        stats = collector.get_stats()
        assert stats["total"] == 0
        assert stats["avg_rating"] == 0.0

    def test_with_data(self, collector):
        collector.collect(
            session_id="s1", query_id="q1", nl_input="a",
            sql_generated="SELECT 1", rating=4, execution_success=True,
        )
        collector.collect(
            session_id="s2", query_id="q2", nl_input="b",
            sql_generated="SELECT 2", rating=2, execution_success=False,
        )
        collector.collect(
            session_id="s3", query_id="q3", nl_input="c",
            sql_generated="SELECT 3", rating=0, feedback_text="ok",
        )
        stats = collector.get_stats()
        assert stats["total"] == 3
        assert stats["avg_rating"] == 3.0  # (4+2)/2
        assert abs(stats["success_rate"] - 100 / 3) < 1.0  # rounded to 1dp
        assert stats["with_feedback"] == 1

    def test_domain_filtered(self, collector):
        collector.collect(
            session_id="s1", query_id="q1", nl_input="a",
            sql_generated="SELECT 1", rating=5, domain_id="ecommerce",
        )
        collector.collect(
            session_id="s2", query_id="q2", nl_input="b",
            sql_generated="SELECT 2", rating=1, domain_id="finance",
        )
        stats = collector.get_stats(domain="ecommerce")
        assert stats["total"] == 1
        assert stats["avg_rating"] == 5.0


class TestExportForAnalysis:
    def test_export_all(self, collector):
        collector.collect(session_id="s1", query_id="q1", nl_input="a", sql_generated="SELECT 1")
        collector.collect(session_id="s2", query_id="q2", nl_input="b", sql_generated="SELECT 2")
        data = collector.export_for_analysis()
        assert len(data) == 2

    def test_export_domain_filtered(self, collector):
        collector.collect(
            session_id="s1", query_id="q1", nl_input="a",
            sql_generated="SELECT 1", domain_id="ecom",
        )
        collector.collect(
            session_id="s2", query_id="q2", nl_input="b",
            sql_generated="SELECT 2", domain_id="fin",
        )
        data = collector.export_for_analysis(domain="ecom")
        assert len(data) == 1
        assert data[0]["domain_id"] == "ecom"

    def test_export_min_rating(self, collector):
        collector.collect(
            session_id="s1", query_id="q1", nl_input="a",
            sql_generated="SELECT 1", rating=1,
        )
        collector.collect(
            session_id="s2", query_id="q2", nl_input="b",
            sql_generated="SELECT 2", rating=5,
        )
        data = collector.export_for_analysis(min_rating=4)
        assert len(data) == 1
        assert data[0]["rating"] == 5


class TestCount:
    def test_count_empty(self, collector):
        assert collector.count() == 0

    def test_count_with_records(self, collector):
        for i in range(3):
            collector.collect(
                session_id=f"s{i}", query_id=f"q{i}", nl_input=f"q{i}",
                sql_generated="SELECT 1",
            )
        assert collector.count() == 3

    def test_count_domain_filtered(self, collector):
        collector.collect(
            session_id="s1", query_id="q1", nl_input="a",
            sql_generated="SELECT 1", domain_id="ecom",
        )
        collector.collect(session_id="s2", query_id="q2", nl_input="b", sql_generated="SELECT 2")
        assert collector.count(domain="ecom") == 1
