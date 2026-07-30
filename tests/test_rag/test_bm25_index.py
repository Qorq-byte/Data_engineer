"""Tests for BM25Index — SQLite FTS5 backend and BM25Tokenizer.

Uses ``:memory:`` SQLite databases and ``tmp_path`` for file-based instances.
"""

from __future__ import annotations

import pytest

from app.knowledge.retrieval.bm25_index import (
    BM25Index,
    BM25Tokenizer,
    _detect_language,
    _escape_fts5_token,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_doc(doc_id: str, text: str, metadata: dict | None = None) -> dict:
    return {"doc_id": doc_id, "text": text, "metadata": metadata or {}}


# ── Language detection ─────────────────────────────────────────────────


class TestLanguageDetection:
    """CJK ratio language detection (same algorithm as LanguageDetector)."""

    def test_detect_zh(self):
        assert _detect_language("查询所有用户的订单") == "zh"

    def test_detect_en(self):
        assert _detect_language("find all orders from last month") == "en"

    def test_detect_mixed(self):
        # Enough CJK to be >0.1 but ≤0.5
        assert _detect_language("查询 user orders") == "mixed"

    def test_detect_empty(self):
        assert _detect_language("") == "en"

    def test_detect_numeric_only(self):
        assert _detect_language("123 456 789") == "en"


# ── FTS5 token escaping ────────────────────────────────────────────────


class TestEscapeFts5Token:
    def test_plain_token_unchanged(self):
        assert _escape_fts5_token("hello") == "hello"

    def test_asterisk_escaped(self):
        assert _escape_fts5_token("foo*") == r"foo\*"

    def test_parentheses_escaped(self):
        assert _escape_fts5_token("func()") == r"func\(\)"

    def test_operator_quoted(self):
        assert _escape_fts5_token("OR") == '"OR"'

    def test_operator_case_insensitive(self):
        assert _escape_fts5_token("and") == '"and"'


# ── BM25Tokenizer ──────────────────────────────────────────────────────


class TestBM25Tokenizer:
    """Tests for the two-tier (jieba/whitespace) tokenizer."""

    # ── Chinese ────────────────────────────────────────────────────────

    def test_tokenize_zh_basic(self):
        tokens = BM25Tokenizer.tokenize("查询用户订单", language="zh")
        assert "查询" in tokens
        assert "用户" in tokens
        assert "订单" in tokens

    def test_tokenize_zh_filters_stopwords(self):
        tokens = BM25Tokenizer.tokenize("我的订单", language="zh")
        assert "我" not in tokens  # single char + stopword
        assert "的" not in tokens  # stopword
        assert "订单" in tokens

    def test_tokenize_zh_filters_single_char(self):
        # jieba segments "看数据表" as "数据表" (data table), filtering "看" as single char
        tokens = BM25Tokenizer.tokenize("看数据表", language="zh")
        assert "看" not in tokens  # single char filtered
        # "数据表" is a multi-char token that should survive
        assert any(len(t) >= 2 for t in tokens)

    def test_tokenize_zh_empty(self):
        assert BM25Tokenizer.tokenize("", language="zh") == []

    # ── English ────────────────────────────────────────────────────────

    def test_tokenize_en_basic(self):
        tokens = BM25Tokenizer.tokenize("find all user orders", language="en")
        assert "find" in tokens
        assert "user" in tokens
        assert "orders" in tokens

    def test_tokenize_en_filters_stopwords(self):
        tokens = BM25Tokenizer.tokenize("the quick brown fox", language="en")
        assert "the" not in tokens
        assert "quick" in tokens
        assert "brown" in tokens
        assert "fox" in tokens

    def test_tokenize_en_lowercases(self):
        tokens = BM25Tokenizer.tokenize("Sales Revenue Monthly", language="en")
        assert "sales" in tokens
        assert "revenue" in tokens
        assert "monthly" in tokens

    def test_tokenize_en_strips_punctuation(self):
        tokens = BM25Tokenizer.tokenize("hello, world! how's it going?", language="en")
        for t in tokens:
            assert "," not in t
            assert "!" not in t
            assert "?" not in t
            assert "'" not in t

    def test_tokenize_en_empty(self):
        assert BM25Tokenizer.tokenize("", language="en") == []

    # ── Mixed ──────────────────────────────────────────────────────────

    def test_tokenize_mixed_combines_both(self):
        tokens = BM25Tokenizer.tokenize("查询 user 订单 revenue", language="mixed")
        # Should have Chinese tokens
        assert "查询" in tokens or "订单" in tokens
        # Should have English tokens
        has_en = any(t in tokens for t in ["user", "revenue"])
        assert has_en

    # ── Auto-detection ─────────────────────────────────────────────────

    def test_tokenize_auto_detects_zh(self):
        tokens = BM25Tokenizer.tokenize("查询用户订单", language="auto")
        assert len(tokens) > 0
        # All tokens should be Chinese words (no single chars)
        for t in tokens:
            assert len(t) >= 2 or t.isascii()

    def test_tokenize_auto_detects_en(self):
        tokens = BM25Tokenizer.tokenize("monthly sales report", language="auto")
        assert "monthly" in tokens
        assert "sales" in tokens
        assert "report" in tokens

    # ── tokenize_for_index ─────────────────────────────────────────────

    def test_tokenize_for_index_returns_joined_string(self):
        result = BM25Tokenizer.tokenize_for_index("hello world", language="en")
        assert isinstance(result, str)
        assert "hello" in result
        assert "world" in result

    def test_tokenize_for_index_space_separated(self):
        result = BM25Tokenizer.tokenize_for_index("查询用户订单", language="zh")
        parts = result.split()
        assert len(parts) >= 1
        # Each part should be a multi-char Chinese word (or single valid char)
        for p in parts:
            assert len(p) >= 1

    # ── build_fts5_query ───────────────────────────────────────────────

    def test_build_fts5_query_or_joined(self):
        query = BM25Tokenizer.build_fts5_query("monthly sales report", language="en")
        assert " OR " in query
        assert "monthly" in query
        assert "sales" in query
        assert "report" in query

    def test_build_fts5_query_tokens_double_quoted(self):
        query = BM25Tokenizer.build_fts5_query("sales", language="en")
        assert query.startswith('"')
        assert '"sales"' in query

    def test_build_fts5_query_empty_returns_empty_quote(self):
        query = BM25Tokenizer.build_fts5_query("the and or", language="en")
        # All stopwords → empty
        assert query == '""'

    def test_build_fts5_query_multi_word_or_join(self):
        """Multiple content words should be OR-joined with double quotes."""
        query = BM25Tokenizer.build_fts5_query("sales revenue monthly", language="en")
        parts = query.split(" OR ")
        assert len(parts) == 3
        for p in parts:
            assert p.startswith('"') and p.endswith('"')


# ── BM25Index ──────────────────────────────────────────────────────────


class TestBM25Index:
    """Core BM25Index integration tests (in-memory SQLite)."""

    @pytest.fixture
    def index(self) -> BM25Index:
        idx = BM25Index(":memory:")
        idx.connect()
        return idx

    # ── Construction & connection ──────────────────────────────────────

    def test_default_path_is_memory(self):
        idx = BM25Index()
        assert idx.db_path == ":memory:"

    def test_custom_path(self, tmp_path):
        db_path = str(tmp_path / "bm25_test.db")
        idx = BM25Index(db_path)
        assert idx.db_path == db_path

    def test_connect_creates_file(self, tmp_path):
        db_path = str(tmp_path / "bm25_connect.db")
        idx = BM25Index(db_path)
        idx.connect()
        import os
        assert os.path.exists(db_path)

    def test_connect_idempotent(self, index: BM25Index):
        conn1 = index.connect()
        conn2 = index.connect()
        assert conn1 is conn2

    def test_close_resets_connection(self, index: BM25Index):
        index.connect()
        assert index._conn is not None
        index.close()
        assert index._conn is None

    # ── Namespace management ───────────────────────────────────────────

    def test_ensure_namespace_creates_table(self, index: BM25Index):
        index.ensure_namespace("test_ns")
        assert "test_ns" in index.list_namespaces()

    def test_ensure_namespace_idempotent(self, index: BM25Index):
        index.ensure_namespace("test_ns")
        index.ensure_namespace("test_ns")  # no-op, should not raise

    def test_ensure_namespace_invalid_name_raises(self, index: BM25Index):
        with pytest.raises(ValueError, match="Invalid namespace"):
            index.ensure_namespace("bad; DROP TABLE")

    def test_drop_namespace(self, index: BM25Index):
        index.ensure_namespace("test_ns")
        index.drop_namespace("test_ns")
        assert "test_ns" not in index.list_namespaces()

    def test_drop_nonexistent_namespace(self, index: BM25Index):
        index.drop_namespace("nonexistent")  # should not raise

    def test_list_namespaces_initially_empty(self, index: BM25Index):
        assert index.list_namespaces() == []

    def test_list_namespaces_after_ensure(self, index: BM25Index):
        index.ensure_namespace("ns_a")
        index.ensure_namespace("ns_b")
        ns = index.list_namespaces()
        assert "ns_a" in ns
        assert "ns_b" in ns

    # ── Index ──────────────────────────────────────────────────────────

    def test_index_single_document(self, index: BM25Index):
        count = index.index("test_ns", [_make_doc("d1", "hello world")])
        assert count == 1
        assert index.count("test_ns") == 1

    def test_index_multiple_documents(self, index: BM25Index):
        docs = [_make_doc(f"d{i}", f"document number {i}") for i in range(10)]
        count = index.index("test_ns", docs)
        assert count == 10
        assert index.count("test_ns") == 10

    def test_index_empty_list(self, index: BM25Index):
        count = index.index("test_ns", [])
        assert count == 0

    def test_index_overwrites_duplicate_doc_id(self, index: BM25Index):
        index.index("test_ns", [_make_doc("d1", "original text")])
        index.index("test_ns", [_make_doc("d1", "updated text")])
        assert index.count("test_ns") == 1

    def test_index_stores_metadata(self, index: BM25Index):
        index.index("test_ns", [_make_doc("d1", "hello", {"author": "Alice", "year": 2024})])
        results = index.search("test_ns", "hello")
        assert results[0]["metadata"] == {"author": "Alice", "year": 2024}

    # ── Search ─────────────────────────────────────────────────────────

    def test_search_returns_results(self, index: BM25Index):
        index.index("test_ns", [
            _make_doc("d1", "monthly sales report"),
            _make_doc("d2", "quarterly revenue analysis"),
            _make_doc("d3", "employee attendance log"),
        ])
        results = index.search("test_ns", "sales report")
        assert len(results) > 0
        assert results[0]["doc_id"] == "d1"

    def test_search_respects_top_k(self, index: BM25Index):
        docs = [_make_doc(f"d{i}", f"sales data for region {i}") for i in range(20)]
        index.index("test_ns", docs)
        results = index.search("test_ns", "sales data", top_k=5)
        assert len(results) == 5

    def test_search_empty_query(self, index: BM25Index):
        index.index("test_ns", [_make_doc("d1", "hello world")])
        results = index.search("test_ns", "the and or", language="en")
        # All stopwords → empty query → no results
        assert results == []

    def test_search_empty_namespace(self, index: BM25Index):
        index.ensure_namespace("test_ns")
        results = index.search("test_ns", "hello")
        assert results == []

    def test_search_returns_scores(self, index: BM25Index):
        index.index("test_ns", [
            _make_doc("d1", "sales revenue monthly report"),
            _make_doc("d2", "employee attendance records"),
        ])
        results = index.search("test_ns", "sales revenue")
        assert len(results) >= 1
        for r in results:
            assert isinstance(r["score"], (int, float))
            assert r["score"] >= 0  # positive = better match

    def test_search_better_match_scores_higher(self, index: BM25Index):
        index.index("test_ns", [
            _make_doc("exact", "monthly sales revenue report"),
            _make_doc("partial", "annual revenue breakdown"),
        ])
        results = index.search("test_ns", "monthly sales revenue report")
        if len(results) >= 2:
            # exact match should score higher than partial
            assert results[0]["doc_id"] == "exact"

    # ── Chinese search ─────────────────────────────────────────────────

    def test_index_and_search_chinese(self, index: BM25Index):
        index.index("test_ns", [
            _make_doc("d1", "月度销售报告"),
            _make_doc("d2", "员工考勤记录"),
            _make_doc("d3", "季度营收分析"),
        ], language="zh")
        results = index.search("test_ns", "销售报告", language="zh")
        assert len(results) > 0
        assert results[0]["doc_id"] == "d1"

    def test_chinese_cross_language_no_match(self, index: BM25Index):
        """English query should not match Chinese-indexed docs (and vice versa)."""
        index.index("test_ns", [_make_doc("d1", "月度销售报告")], language="zh")
        results = index.search("test_ns", "sales report", language="en")
        # English tokens won't match Chinese tokens
        assert len(results) == 0 or results[0]["doc_id"] != "d1"

    # ── Delete ─────────────────────────────────────────────────────────

    def test_delete_existing_document(self, index: BM25Index):
        index.index("test_ns", [_make_doc("d1", "hello world")])
        deleted = index.delete("test_ns", "d1")
        assert deleted == 1
        assert index.count("test_ns") == 0

    def test_delete_nonexistent_document(self, index: BM25Index):
        index.ensure_namespace("test_ns")
        deleted = index.delete("test_ns", "nonexistent")
        assert deleted == 0

    # ── Clear ──────────────────────────────────────────────────────────

    def test_clear_removes_all_documents(self, index: BM25Index):
        index.index("test_ns", [
            _make_doc("d1", "hello"),
            _make_doc("d2", "world"),
        ])
        index.clear("test_ns")
        assert index.count("test_ns") == 0

    def test_clear_preserves_table(self, index: BM25Index):
        """After clear, the namespace should still be usable."""
        index.index("test_ns", [_make_doc("d1", "hello")])
        index.clear("test_ns")
        # Should be able to index again
        index.index("test_ns", [_make_doc("d2", "world")])
        assert index.count("test_ns") == 1

    # ── Count ──────────────────────────────────────────────────────────

    def test_count_nonexistent_namespace(self, index: BM25Index):
        # Should return 0 rather than raise
        assert index.count("nonexistent") == 0


class TestBM25IndexCrossNamespace:
    """Cross-namespace isolation tests."""

    @pytest.fixture
    def index(self) -> BM25Index:
        idx = BM25Index(":memory:")
        idx.connect()
        return idx

    def test_namespaces_are_isolated(self, index: BM25Index):
        index.index("ns_a", [
            _make_doc("a1", "sales report"),
            _make_doc("a2", "revenue data"),
        ])
        index.index("ns_b", [
            _make_doc("b1", "employee records"),
        ])
        assert index.count("ns_a") == 2
        assert index.count("ns_b") == 1

        # Deleting from ns_a should not affect ns_b
        index.delete("ns_a", "a1")
        assert index.count("ns_a") == 1
        assert index.count("ns_b") == 1

    def test_same_doc_id_different_namespaces(self, index: BM25Index):
        index.index("ns_a", [_make_doc("d1", "sales in ns_a")])
        index.index("ns_b", [_make_doc("d1", "sales in ns_b")])
        assert index.count("ns_a") == 1
        assert index.count("ns_b") == 1

        # Deleting d1 from ns_a should leave ns_b's d1 intact
        index.delete("ns_a", "d1")
        assert index.count("ns_a") == 0
        assert index.count("ns_b") == 1


class TestBM25IndexFileBased:
    """Tests using file-based SQLite (tmp_path)."""

    @pytest.fixture
    def index(self, tmp_path) -> BM25Index:
        db_path = str(tmp_path / "bm25_file.db")
        idx = BM25Index(db_path)
        idx.connect()
        return idx

    def test_data_persists_across_reconnect(self, tmp_path):
        db_path = str(tmp_path / "bm25_persist.db")

        # First session
        idx1 = BM25Index(db_path)
        idx1.connect()
        idx1.index("test_ns", [_make_doc("d1", "persistent data")])
        idx1.close()

        # Second session — same file
        idx2 = BM25Index(db_path)
        idx2.connect()
        assert "test_ns" in idx2.list_namespaces()
        assert idx2.count("test_ns") == 1
        results = idx2.search("test_ns", "persistent data")
        assert len(results) == 1
        assert results[0]["doc_id"] == "d1"
        idx2.close()

    def test_file_database_creates_parent_dir(self, tmp_path):
        db_dir = tmp_path / "nested" / "dirs"
        db_path = str(db_dir / "index.db")
        idx = BM25Index(db_path)
        idx.connect()
        assert db_dir.exists()
        assert db_dir.is_dir()
