"""Tests for RRFFusion — reciprocal rank fusion engine.

Covers rank merging, alpha weighting, edge cases, and determinism.
"""

from __future__ import annotations

import pytest

from app.knowledge.retrieval.rrf_fusion import DEFAULT_ALPHA, DEFAULT_K, RRFFusion

# ── Helpers ────────────────────────────────────────────────────────────


def _dense(doc_ids: list[str]) -> list[dict]:
    """Build a ranked dense (LanceDB) result list."""
    return [{"doc_id": did, "text": f"dense {did}", "_distance": 0.01 * (i + 1)}
            for i, did in enumerate(doc_ids)]


def _sparse(doc_ids: list[str]) -> list[dict]:
    """Build a ranked sparse (BM25) result list."""
    return [{"doc_id": did, "text": f"sparse {did}", "score": 10.0 - i}
            for i, did in enumerate(doc_ids)]


# ── Construction ───────────────────────────────────────────────────────


class TestRRFFusionConstruction:
    def test_default_values(self):
        f = RRFFusion()
        assert f.k == DEFAULT_K
        assert f.alpha == DEFAULT_ALPHA

    def test_custom_k(self):
        f = RRFFusion(k=30)
        assert f.k == 30

    def test_custom_alpha(self):
        f = RRFFusion(alpha=0.7)
        assert f.alpha == 0.7

    def test_k_must_be_positive(self):
        with pytest.raises(ValueError, match="k must be"):
            RRFFusion(k=0)

    def test_alpha_must_be_in_range(self):
        with pytest.raises(ValueError, match="alpha must be"):
            RRFFusion(alpha=1.5)

    def test_alpha_zero_is_valid(self):
        f = RRFFusion(alpha=0.0)
        assert f.alpha == 0.0

    def test_alpha_one_is_valid(self):
        f = RRFFusion(alpha=1.0)
        assert f.alpha == 1.0


# ── Basic fusion ───────────────────────────────────────────────────────


class TestRRFFusionBasic:
    @pytest.fixture
    def fusion(self) -> RRFFusion:
        return RRFFusion()

    def test_fuse_both_lists(self, fusion: RRFFusion):
        dense = _dense(["a", "b", "c"])
        sparse = _sparse(["b", "c", "d"])
        result = fusion.fuse(dense, sparse)
        assert len(result) > 0
        doc_ids = [r["doc_id"] for r in result]
        # "b" is ranked high in both → should be first
        assert doc_ids[0] == "b"

    def test_fuse_dense_only(self, fusion: RRFFusion):
        dense = _dense(["a", "b"])
        result = fusion.fuse(dense, [])
        assert len(result) == 2
        assert result[0]["doc_id"] == "a"

    def test_fuse_sparse_only(self, fusion: RRFFusion):
        sparse = _sparse(["x", "y"])
        result = fusion.fuse([], sparse)
        assert len(result) == 2
        assert result[0]["doc_id"] == "x"

    def test_fuse_both_empty(self, fusion: RRFFusion):
        result = fusion.fuse([], [])
        assert result == []

    def test_fuse_preserves_original_fields(self, fusion: RRFFusion):
        dense = [{"doc_id": "a", "text": "hello", "_distance": 0.05}]
        sparse = [{"doc_id": "a", "text": "sparse_hello", "score": 8.5}]
        result = fusion.fuse(dense, sparse)
        # Dense takes precedence for field values
        assert result[0]["text"] == "hello"
        assert result[0]["_distance"] == 0.05

    def test_fuse_injects_rrf_metadata(self, fusion: RRFFusion):
        dense = _dense(["a", "b"])
        sparse = _sparse(["c", "a"])
        result = fusion.fuse(dense, sparse)
        for r in result:
            assert "rrf_score" in r
            assert "dense_rank" in r
            assert "sparse_rank" in r
            assert isinstance(r["rrf_score"], float)
            assert r["rrf_score"] > 0

    # ── top_k ──────────────────────────────────────────────────────────

    def test_fuse_respects_top_k(self, fusion: RRFFusion):
        dense = _dense([f"d{i}" for i in range(20)])
        sparse = _sparse([f"s{i}" for i in range(20)])
        result = fusion.fuse(dense, sparse, top_k=5)
        assert len(result) == 5

    def test_fuse_top_k_one(self, fusion: RRFFusion):
        dense = _dense(["a", "b"])
        sparse = _sparse(["b", "c"])
        result = fusion.fuse(dense, sparse, top_k=1)
        assert len(result) == 1

    def test_fuse_top_k_zero_returns_empty(self, fusion: RRFFusion):
        result = fusion.fuse(_dense(["a"]), _sparse(["a"]), top_k=0)
        assert result == []

    # ── Ordering ───────────────────────────────────────────────────────

    def test_fuse_mutual_match_ranks_highest(self, fusion: RRFFusion):
        """Doc appearing in BOTH lists at high ranks should win."""
        dense = _dense(["common", "d1", "d2"])
        sparse = _sparse(["common", "s1", "s2"])
        result = fusion.fuse(dense, sparse)
        assert result[0]["doc_id"] == "common"

    def test_fuse_deterministic_tiebreak(self, fusion: RRFFusion):
        """Same ranks → tiebreak by doc_id for deterministic output."""
        dense = _dense(["a", "b"])
        sparse = _sparse(["a", "b"])
        result1 = fusion.fuse(dense, sparse)
        result2 = fusion.fuse(dense, sparse)
        assert result1 == result2


# ── Alpha weighting ────────────────────────────────────────────────────


class TestRRFFusionAlpha:
    def test_alpha_dense_only(self):
        """alpha=1.0 should rank purely by dense order."""
        f = RRFFusion(alpha=1.0)
        dense = _dense(["a", "b", "c"])
        sparse = _sparse(["c", "b", "a"])
        result = f.fuse(dense, sparse)
        assert result[0]["doc_id"] == "a"
        assert result[1]["doc_id"] == "b"

    def test_alpha_sparse_only(self):
        """alpha=0.0 should rank purely by sparse order."""
        f = RRFFusion(alpha=0.0)
        dense = _dense(["a", "b", "c"])
        sparse = _sparse(["c", "b", "a"])
        result = f.fuse(dense, sparse)
        assert result[0]["doc_id"] == "c"
        assert result[1]["doc_id"] == "b"

    def test_alpha_balanced(self):
        """alpha=0.5 balances both — mutual top-1 from each should be close."""
        f = RRFFusion(alpha=0.5)
        dense = _dense(["shared", "d1"])
        sparse = _sparse(["shared", "s1"])
        result = f.fuse(dense, sparse)
        assert result[0]["doc_id"] == "shared"

    def test_alpha_dense_heavy(self):
        """alpha=0.8 favours dense ordering."""
        f = RRFFusion(alpha=0.8)
        dense = _dense(["a", "b", "c"])
        sparse = _sparse(["c", "b", "a"])
        result = f.fuse(dense, sparse)
        # a is #1 in dense, #3 in sparse → should still rank high
        assert result[0]["doc_id"] == "a"

    def test_alpha_sparse_heavy(self):
        """alpha=0.2 favours sparse ordering."""
        f = RRFFusion(alpha=0.2)
        dense = _dense(["a", "b", "c"])
        sparse = _sparse(["c", "b", "a"])
        result = f.fuse(dense, sparse)
        # c is #1 in sparse, #3 in dense → should rank high
        assert result[0]["doc_id"] == "c"


# ── k parameter ────────────────────────────────────────────────────────


class TestRRFFusionK:
    def test_small_k_increases_rank_impact(self):
        """Small k → the score ratio between rank-1 and rank-2 is larger."""
        f_small = RRFFusion(k=1, alpha=1.0)   # dense-only for clean measurement
        f_large = RRFFusion(k=100, alpha=1.0)

        dense = _dense(["top_ranked", "second_ranked"])

        r_small = f_small.fuse(dense, [])
        r_large = f_large.fuse(dense, [])

        # ratio of #1 score to #2 score should be larger with smaller k
        ratio_small = r_small[0]["rrf_score"] / r_small[1]["rrf_score"]
        ratio_large = r_large[0]["rrf_score"] / r_large[1]["rrf_score"]
        assert ratio_small > ratio_large

    def test_large_k_flattens_scores(self):
        """Large k → scores are closer together (more democratic)."""
        f = RRFFusion(k=1000, alpha=0.5)
        dense = _dense(["a", "b", "c", "d", "e"])
        sparse = _sparse(["e", "d", "c", "b", "a"])
        result = f.fuse(dense, sparse)
        scores = [r["rrf_score"] for r in result]
        # All scores should be very close
        if len(scores) >= 2:
            max_diff = max(scores) - min(scores)
            assert max_diff < 0.01


# ── Edge cases ─────────────────────────────────────────────────────────


class TestRRFFusionEdgeCases:
    @pytest.fixture
    def fusion(self) -> RRFFusion:
        return RRFFusion()

    def test_doc_in_only_one_list(self, fusion: RRFFusion):
        """Document appearing in dense only should get sparse rank = None."""
        dense = _dense(["only_dense", "common"])
        sparse = _sparse(["common"])
        result = fusion.fuse(dense, sparse)
        only = next(r for r in result if r["doc_id"] == "only_dense")
        assert only["dense_rank"] is not None
        assert only["sparse_rank"] is None
        assert only["rrf_score"] > 0

    def test_no_overlap_concatenates(self, fusion: RRFFusion):
        """When there's no overlap, results from both lists are merged."""
        dense = _dense(["a", "b"])
        sparse = _sparse(["c", "d"])
        result = fusion.fuse(dense, sparse)
        doc_ids = {r["doc_id"] for r in result}
        assert doc_ids == {"a", "b", "c", "d"}

    def test_duplicate_doc_id_in_same_list(self, fusion: RRFFusion):
        """First occurrence of a doc_id takes the rank; later ones ignored."""
        dense = [
            {"doc_id": "dup", "text": "first"},
            {"doc_id": "dup", "text": "second"},
            {"doc_id": "other", "text": "other"},
        ]
        sparse = _sparse(["other"])
        result = fusion.fuse(dense, sparse)
        dup = next(r for r in result if r["doc_id"] == "dup")
        assert dup["dense_rank"] == 1  # first occurrence, rank 1
        assert dup["text"] == "first"

    def test_none_doc_id_ignored(self, fusion: RRFFusion):
        """Docs with None doc_id are silently skipped."""
        dense = [{"doc_id": None, "text": "bad"}]  # type: ignore[arg-type]
        sparse = _sparse(["a"])
        result = fusion.fuse(dense, sparse)
        doc_ids = {r["doc_id"] for r in result}
        assert None not in doc_ids
        assert "a" in doc_ids

    def test_many_documents(self, fusion: RRFFusion):
        """Stress test with 500 dense + 500 sparse results."""
        dense = _dense([f"d{i}" for i in range(500)])
        sparse = _sparse([f"s{i}" for i in range(500)])
        result = fusion.fuse(dense, sparse, top_k=20)
        assert len(result) == 20

    def test_sparse_field_fills_missing_dense(self, fusion: RRFFusion):
        """When a doc only appears in sparse, its fields are preserved."""
        dense = _dense(["a"])
        sparse = [{"doc_id": "b", "text": "sparse_b", "score": 9.5, "extra": 42}]
        result = fusion.fuse(dense, sparse)
        b = next(r for r in result if r["doc_id"] == "b")
        assert b["text"] == "sparse_b"
        assert b["score"] == 9.5
        assert b["extra"] == 42


# ── Calculated RRF score validation ────────────────────────────────────


class TestRRFFusionScoreCalculation:
    """Verify RRF scores match the formula exactly."""

    def test_score_formula(self):
        """Manually compute expected RRF score and compare."""
        f = RRFFusion(k=60, alpha=0.5)

        dense = _dense(["a"])  # rank 1 in dense
        sparse = _sparse(["a"])  # rank 1 in sparse

        result = f.fuse(dense, sparse)
        expected = 0.5 / (60 + 1) + 0.5 / (60 + 1)
        assert result[0]["rrf_score"] == pytest.approx(expected)

    def test_absent_from_dense(self):
        f = RRFFusion(k=60, alpha=0.5)
        result = f.fuse([], _sparse(["a"]))
        # Only sparse contributes: (1 - 0.5) / (60 + 1)
        expected = 0.5 / 61
        assert result[0]["rrf_score"] == pytest.approx(expected)

    def test_absent_from_sparse(self):
        f = RRFFusion(k=60, alpha=0.5)
        result = f.fuse(_dense(["a"]), [])
        # Only dense contributes: 0.5 / (60 + 1)
        expected = 0.5 / 61
        assert result[0]["rrf_score"] == pytest.approx(expected)

    def test_score_decreases_with_rank(self):
        """Rank 1 should give a higher partial score than rank 2."""
        f = RRFFusion(alpha=1.0)  # dense only, for isolation
        dense = _dense(["a", "b"])
        result = f.fuse(dense, [])
        assert result[0]["rrf_score"] > result[1]["rrf_score"]
