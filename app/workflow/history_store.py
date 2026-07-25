"""Plan history store — SQLite-backed query→plan history for L2 routing.

Used by ``PlanSelector`` (L2 layer) to look up which workflow plan similar
past queries resolved to.  Similarity is deterministic and cheap:

1. **Exact match** — queries are normalized (lowercased, punctuation stripped,
   whitespace collapsed) and compared verbatim; exact matches rank first with
   similarity 1.0.
2. **Token overlap** — otherwise queries are tokenized (jieba for Chinese,
   whitespace for English — via ``BM25Tokenizer``) and compared with Jaccard
   overlap; candidates with overlap >= 0.6 count as similar.

Usage::

    store = PlanHistoryStore(":memory:")  # or a file path
    store.record("monthly sales by region", "gensql_agentic", domain="sales")
    matches = store.find_similar("monthly sales by region")
    # [HistoryRecord(plan_id="gensql_agentic", similarity=1.0, ...)]
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.knowledge.retrieval.bm25_index import BM25Tokenizer

# ── Constants ──────────────────────────────────────────────────────────

# Minimum Jaccard token overlap to consider two queries similar
JACCARD_THRESHOLD = 0.6

# Punctuation / symbols stripped during normalization (\w covers CJK)
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize_query(text: str) -> str:
    """Normalize *text* for exact-match comparison.

    Lowercases, strips punctuation, and collapses whitespace.
    """
    clean = _PUNCT_RE.sub(" ", text.lower())
    return _WS_RE.sub(" ", clean).strip()


def _tokenize(text: str) -> set[str]:
    """Tokenize *text* into a set of comparison tokens.

    Delegates to ``BM25Tokenizer`` (jieba for Chinese, whitespace for
    English).  Falls back to a plain whitespace split of the normalized
    text when the tokenizer filters everything out (e.g. stopword-only
    queries), so short queries still produce comparable token sets.
    """
    tokens = set(BM25Tokenizer.tokenize(text))
    if not tokens:
        tokens = set(normalize_query(text).split())
    return tokens


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Jaccard overlap |A∩B| / |A∪B| between two token sets (0.0 if empty)."""
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# ── Records ────────────────────────────────────────────────────────────


@dataclass
class HistoryRecord:
    """A single plan-history entry returned by ``find_similar``.

    Attributes:
        query_text: The original query text as recorded.
        plan_id: The workflow plan the query used.
        domain: Optional domain the query belonged to.
        success: Whether the workflow run succeeded.
        created_at: ISO-8601 UTC timestamp of the recording.
        similarity: Similarity to the lookup query (1.0 = exact match).
    """

    query_text: str
    plan_id: str
    domain: str | None = None
    success: bool = True
    created_at: str = ""
    similarity: float = 0.0


# ── Store ──────────────────────────────────────────────────────────────


class PlanHistoryStore:
    """SQLite-backed store of query→plan history for L2 plan selection.

    Supports both ``":memory:"`` and on-disk database paths.  The
    connection is created with ``check_same_thread=False`` so the store
    can be shared across async task threads.

    Args:
        db_path: SQLite database path (default ``":memory:"``).
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    # ── Schema ───────────────────────────────────────────────────────

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS plan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                domain TEXT,
                success INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_plan_history_normalized
                ON plan_history (normalized_text);
            """
        )
        self._conn.commit()

    # ── Public API ───────────────────────────────────────────────────

    def record(
        self,
        query_text: str,
        plan_id: str,
        domain: str | None = None,
        success: bool = True,
    ) -> None:
        """Record that *query_text* was routed to *plan_id*.

        Args:
            query_text: The user's natural-language query.
            plan_id: The workflow plan that was used.
            domain: Optional domain the query belonged to.
            success: Whether the workflow run succeeded.
        """
        self._conn.execute(
            "INSERT INTO plan_history "
            "(query_text, normalized_text, plan_id, domain, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                query_text,
                normalize_query(query_text),
                plan_id,
                domain,
                1 if success else 0,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()

    def find_similar(
        self,
        query_text: str,
        domain: str | None = None,
        limit: int = 5,
    ) -> list[HistoryRecord]:
        """Find past queries similar to *query_text*.

        Exact normalized matches rank first (similarity 1.0); remaining
        slots are filled with Jaccard token-overlap matches at or above
        ``JACCARD_THRESHOLD``, ordered by similarity then recency.

        Args:
            query_text: The query to match against history.
            domain: Optional domain filter (``None`` = all domains).
            limit: Maximum number of records to return.

        Returns:
            Up to *limit* ``HistoryRecord`` entries, best match first.
        """
        normalized = normalize_query(query_text)
        if not normalized or limit <= 0:
            return []

        domain_clause = "" if domain is None else " AND domain = ?"
        domain_params: tuple[str, ...] = () if domain is None else (domain,)

        # Tier 1: exact normalized match
        rows = self._conn.execute(
            "SELECT * FROM plan_history WHERE normalized_text = ?"
            + domain_clause
            + " ORDER BY id DESC LIMIT ?",
            (normalized, *domain_params, limit),
        ).fetchall()
        results = [self._to_record(r, similarity=1.0) for r in rows]

        if len(results) >= limit:
            return results

        # Tier 2: Jaccard token overlap on remaining rows
        query_tokens = _tokenize(query_text)
        rows = self._conn.execute(
            "SELECT * FROM plan_history WHERE normalized_text != ?" + domain_clause,
            (normalized, *domain_params),
        ).fetchall()

        scored: list[tuple[float, int, sqlite3.Row]] = []
        for row in rows:
            sim = jaccard_similarity(query_tokens, _tokenize(row["query_text"]))
            if sim >= JACCARD_THRESHOLD:
                scored.append((sim, row["id"], row))
        scored.sort(key=lambda item: (-item[0], -item[1]))

        for sim, _, row in scored[: limit - len(results)]:
            results.append(self._to_record(row, similarity=round(sim, 4)))
        return results

    def count(self) -> int:
        """Return the total number of recorded entries."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM plan_history").fetchone()
        return int(row["n"])

    def clear(self) -> None:
        """Delete all recorded entries."""
        self._conn.execute("DELETE FROM plan_history")
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _to_record(row: sqlite3.Row, similarity: float) -> HistoryRecord:
        return HistoryRecord(
            query_text=row["query_text"],
            plan_id=row["plan_id"],
            domain=row["domain"],
            success=bool(row["success"]),
            created_at=row["created_at"],
            similarity=similarity,
        )
