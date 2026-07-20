"""RRF (Reciprocal Rank Fusion) engine — hybrid search result merging.

See SPEC §4.2.4 and implementation-plan.md §4.8.4 for the full RAG architecture.

RRF combines dense (LanceDB) and sparse (BM25) search results into a single
ranked list without requiring score calibration.  The algorithm is parameter-free
aside from *k* (smoothing constant) and *α* (dense/sparse weight).

**Formula** (per document *d*)::

    rrf(d) = α / (k + rank_dense(d)) + (1 - α) / (k + rank_sparse(d))

Where:
    - rank is 1-indexed (1 = best match), or 0 if the document is absent
    - k = 60 (default, dampens rank differences)
    - α ∈ [0, 1] controls dense-vs-sparse weight (0.5 = balanced)

Usage::

    fusion = RRFFusion(k=60, alpha=0.5)
    merged = fusion.fuse(
        dense_results=[{"doc_id": "a", ...}, {"doc_id": "b", ...}],
        sparse_results=[{"doc_id": "b", ...}, {"doc_id": "c", ...}],
        top_k=10,
    )
"""

from __future__ import annotations

from typing import Any

# ── Constants ──────────────────────────────────────────────────────────

DEFAULT_K = 60  # RRF smoothing constant
DEFAULT_ALPHA = 0.5  # balanced dense/sparse weighting


# ── RRFFusion ──────────────────────────────────────────────────────────


class RRFFusion:
    """Reciprocal Rank Fusion engine for hybrid (dense + sparse) search.

    Merges two ranked result lists using the RRF formula.  Because RRF operates
    on ranks rather than raw scores, the two backends can have completely
    different score distributions — no normalisation is needed.

    Args:
        k: Smoothing constant (default 60).  Larger values make rank differences
            less significant, giving more weight to documents that appear in only
            one list.
        alpha: Dense-vs-sparse weight, in ``[0, 1]``.
            - ``0.0`` — sparse-only (ignore dense results)
            - ``0.5`` — balanced (default)
            - ``1.0`` — dense-only (ignore sparse results)
    """

    def __init__(self, k: int = DEFAULT_K, alpha: float = DEFAULT_ALPHA):
        if k < 1:
            raise ValueError(f"k must be ≥ 1, got {k}")
        if not 0.0 <= alpha <= 1.0:
            raise ValueError(f"alpha must be in [0, 1], got {alpha}")
        self._k = k
        self._alpha = alpha

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def k(self) -> int:
        return self._k

    @property
    def alpha(self) -> float:
        return self._alpha

    # ── Fusion ─────────────────────────────────────────────────────────

    def fuse(
        self,
        dense_results: list[dict[str, Any]],
        sparse_results: list[dict[str, Any]],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Fuse two ranked result lists via RRF and return the top *top_k*.

        The input lists are assumed to be **already ranked** (index 0 = best).
        Each dict **must** contain a ``"doc_id"`` key.  All other keys from the
        first occurrence of a doc_id are retained in the output.

        Args:
            dense_results: Ranked list from LanceDB / vector search.
            sparse_results: Ranked list from BM25 / keyword search.
            top_k: Maximum number of fused results to return.

        Returns:
            Merged list sorted by RRF score (descending), each dict containing
            all original fields plus:
                - ``rrf_score`` (float): The fused RRF score.
                - ``dense_rank`` (int | None): Rank in dense list (1 = best).
                - ``sparse_rank`` (int | None): Rank in sparse list (1 = best).
        """
        if top_k < 1:
            return []

        # Build rank maps: doc_id → (1-indexed rank, original dict)
        dense_ranks, dense_docs = self._build_rank_map(dense_results)
        sparse_ranks, sparse_docs = self._build_rank_map(sparse_results)

        # Collect all unique doc_ids
        all_ids: set[str] = set(dense_ranks) | set(sparse_ranks)

        # Compute RRF score for each document
        scored: list[dict[str, Any]] = []
        for doc_id in all_ids:
            d_rank = dense_ranks.get(doc_id)  # int | None
            s_rank = sparse_ranks.get(doc_id)  # int | None

            rrf_score = self._compute_rrf(d_rank, s_rank)

            # Merge metadata: prefer the first occurrence (dense wins tie)
            merged = self._merge_doc(dense_docs.get(doc_id), sparse_docs.get(doc_id))
            merged["rrf_score"] = rrf_score
            merged["dense_rank"] = d_rank
            merged["sparse_rank"] = s_rank
            scored.append(merged)

        # Sort by RRF score descending, then by doc_id for determinism
        scored.sort(key=lambda d: (-d["rrf_score"], d["doc_id"]))
        return scored[:top_k]

    # ── Internal helpers ───────────────────────────────────────────────

    @staticmethod
    def _build_rank_map(
        results: list[dict[str, Any]],
    ) -> tuple[dict[str, int], dict[str, dict[str, Any]]]:
        """Build doc_id → (1-indexed rank) and doc_id → (original dict) maps.

        Only the *first* occurrence of each doc_id is kept (idempotent against
        accidental duplicates in the same result list).
        """
        rank_map: dict[str, int] = {}
        doc_map: dict[str, dict[str, Any]] = {}
        for rank, doc in enumerate(results, start=1):
            doc_id = doc.get("doc_id")
            if doc_id is not None and doc_id not in rank_map:
                rank_map[doc_id] = rank
                doc_map[doc_id] = dict(doc)
        return rank_map, doc_map

    def _compute_rrf(self, dense_rank: int | None, sparse_rank: int | None) -> float:
        """Compute the weighted RRF score for a single document.

        Args:
            dense_rank: 1-indexed rank in dense list, or ``None`` if absent.
            sparse_rank: 1-indexed rank in sparse list, or ``None`` if absent.

        Returns:
            RRF score (higher = better).
        """
        score = 0.0
        if dense_rank is not None:
            score += self._alpha / (self._k + dense_rank)
        if sparse_rank is not None:
            score += (1.0 - self._alpha) / (self._k + sparse_rank)
        return score

    @staticmethod
    def _merge_doc(
        primary: dict[str, Any] | None,
        secondary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Merge two result dicts for the same doc_id.

        *primary* takes precedence for field values; *secondary* fills in
        fields not present in *primary*.
        """
        if primary is not None:
            base = dict(secondary or {})
            base.update(primary)
            return base
        if secondary is not None:
            return dict(secondary)
        return {}
