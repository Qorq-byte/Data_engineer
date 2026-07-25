"""Query pair store — persistent NL→SQL pair storage with embedding generation.

See SPEC §4.6.2 (query pair storage) and §5.2 (Phase 5 roadmap).

Builds on the existing :class:`~app.cache.query_cache.QueryPairCache` (L1/L2
cache) and adds persistent storage suitable for RAG training-data export.

Usage::

    from app.learning.query_pair_store import query_pair_store

    await query_pair_store.store(
        nl="上季度各区域销售额排行",
        sql="SELECT region, SUM(amount) FROM orders ...",
        domain="ecommerce",
        rating=5,
    )

    training = query_pair_store.get_training_data(domain="ecommerce", min_rating=4)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── SQL schema ────────────────────────────────────────────────────────────

CREATE_PAIRS_SQL = """
CREATE TABLE IF NOT EXISTS query_pairs (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT DEFAULT '',
    nl_original     TEXT NOT NULL,
    nl_normalized   TEXT,
    sql_final       TEXT NOT NULL,
    sql_hash        TEXT NOT NULL,
    language        TEXT DEFAULT '',
    intent          TEXT DEFAULT '',
    rating          INTEGER DEFAULT 0,
    edit_count      INTEGER DEFAULT 0,
    source          TEXT DEFAULT 'generated',
    embedding_json  TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""

INDEX_PAIRS_DOMAIN = (
    "CREATE INDEX IF NOT EXISTS idx_qp_domain ON query_pairs(domain_id);"
)
INDEX_PAIRS_HASH = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_qp_hash ON query_pairs(domain_id, sql_hash);"
)
INDEX_PAIRS_RATING = (
    "CREATE INDEX IF NOT EXISTS idx_qp_rating ON query_pairs(rating);"
)


# ── helpers ───────────────────────────────────────────────────────────────

def _normalize_sql(sql: str) -> str:
    """Normalize SQL for hashing: lowercase, collapse whitespace."""
    return " ".join(sql.lower().split())


def _hash_sql(sql: str) -> str:
    """SHA256 hex digest of normalized SQL."""
    return hashlib.sha256(_normalize_sql(sql).encode()).hexdigest()


def _normalize_nl(text: str) -> str:
    """Normalize NL query: lowercase, collapse whitespace."""
    return " ".join(text.lower().split())


# ── QueryPairStore ────────────────────────────────────────────────────────


class QueryPairStore:
    """Persistent storage for (NL, SQL) query pairs with optional embeddings.

    Uses SQLite for pair storage and optionally generates embeddings via
    :class:`~app.knowledge.retrieval.embedding.EmbeddingGenerator`.
    """

    def __init__(self, db_path: str = "data/query_pairs.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._embedding_gen: Any = None  # lazy init
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self._db_path), check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        conn.execute(CREATE_PAIRS_SQL)
        conn.execute(INDEX_PAIRS_DOMAIN)
        conn.execute(INDEX_PAIRS_HASH)
        conn.execute(INDEX_PAIRS_RATING)
        conn.commit()

    def _get_embedding_generator(self) -> Any:
        if self._embedding_gen is None:
            try:
                from app.knowledge.retrieval.embedding import EmbeddingGenerator

                self._embedding_gen = EmbeddingGenerator()
            except Exception:
                self._embedding_gen = False  # sentinel: embedding unavailable
        return self._embedding_gen if self._embedding_gen is not False else None

    # ── store ──────────────────────────────────────────────────────────

    async def store(
        self,
        nl: str,
        sql: str,
        domain: str = "",
        rating: int = 0,
        language: str = "",
        intent: str = "",
        source: str = "generated",
        edit_count: int = 0,
    ) -> str:
        """Persist a (NL, SQL) pair and return its record ID.

        Generates an embedding for the NL text if an embedding provider
        is available.  Duplicate (domain, sql_hash) pairs are silently
        updated (UPSERT).

        Args:
            nl: Original natural-language query text.
            sql: Final confirmed SQL.
            domain: Active domain ID.
            rating: User rating (1–5), 0 = unrated.
            language: Detected language code (zh/en/mixed).
            intent: Detected intent type.
            source: ``"generated"``, ``"human_written"``, or ``"edited"``.
            edit_count: Number of user edits applied.

        Returns:
            The pair's SHA256-based ID.
        """
        sql_hash = _hash_sql(sql)
        record_id = hashlib.sha256(
            f"{domain}:{sql_hash}".encode()
        ).hexdigest()[:32]
        nl_norm = _normalize_nl(nl)
        now = datetime.now(UTC).isoformat()

        # Generate embedding if available
        embedding_json: str | None = None
        gen = self._get_embedding_generator()
        if gen is not None:
            try:
                emb = await gen.embed(nl)
                embedding_json = json.dumps(emb)
            except Exception:
                pass

        conn = self._get_conn()
        conn.execute(
            """INSERT INTO query_pairs
               (id, domain_id, nl_original, nl_normalized, sql_final, sql_hash,
                language, intent, rating, edit_count, source, embedding_json,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(domain_id, sql_hash) DO UPDATE SET
                nl_original = excluded.nl_original,
                nl_normalized = excluded.nl_normalized,
                rating = excluded.rating,
                edit_count = excluded.edit_count,
                embedding_json = COALESCE(excluded.embedding_json, query_pairs.embedding_json),
                updated_at = excluded.updated_at""",
            (
                record_id,
                domain,
                nl.strip(),
                nl_norm,
                sql.strip(),
                sql_hash,
                language,
                intent,
                rating,
                edit_count,
                source,
                embedding_json,
                now,
                now,
            ),
        )
        conn.commit()
        return record_id

    async def store_batch(
        self, pairs: list[dict[str, Any]]
    ) -> list[str]:
        """Bulk-insert pairs.  Each dict must have ``nl`` and ``sql`` keys.

        Args:
            pairs: List of dicts with keys matching :meth:`store` arguments.

        Returns:
            List of generated record IDs.
        """
        ids: list[str] = []
        for p in pairs:
            rid = await self.store(
                nl=p["nl"],
                sql=p["sql"],
                domain=p.get("domain", ""),
                rating=p.get("rating", 0),
                language=p.get("language", ""),
                intent=p.get("intent", ""),
                source=p.get("source", "generated"),
                edit_count=p.get("edit_count", 0),
            )
            ids.append(rid)
        return ids

    # ── retrieval ──────────────────────────────────────────────────────

    def get_training_data(
        self,
        domain: str = "",
        min_rating: int = 0,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Export pairs suitable for RAG/fine-tuning training data.

        Args:
            domain: Optional domain filter.
            min_rating: Minimum rating (inclusive). 0 = no filter.
            limit: Maximum records to return.

        Returns:
            List of dicts with ``nl``, ``sql``, ``domain``, ``rating``, etc.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if domain:
            conditions.append("domain_id = ?")
            params.append(domain)
        if min_rating > 0:
            conditions.append("rating >= ?")
            params.append(min_rating)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        conn = self._get_conn()
        rows = conn.execute(
            f"SELECT * FROM query_pairs {where} ORDER BY rating DESC, created_at DESC "
            f"LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def get_embedding_pairs(self, domain: str = "") -> list[dict[str, Any]]:
        """Return pairs that have embeddings, suitable for LanceDB indexing.

        Args:
            domain: Optional domain filter.

        Returns:
            List of dicts with ``nl``, ``sql``, ``embedding`` (parsed list).
        """
        domain_filter = (
            "WHERE domain_id = ? AND embedding_json IS NOT NULL"
            if domain else "WHERE embedding_json IS NOT NULL"
        )
        params: list[Any] = ([domain] if domain else [])

        conn = self._get_conn()
        rows = conn.execute(
            f"SELECT nl_original, sql_final, embedding_json, domain_id, rating "
            f"FROM query_pairs {domain_filter}",
            params,
        ).fetchall()

        result: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if d.get("embedding_json"):
                try:
                    d["embedding"] = json.loads(d["embedding_json"])
                except (json.JSONDecodeError, TypeError):
                    d["embedding"] = None
                del d["embedding_json"]
            result.append(d)
        return result

    def search_similar(
        self, nl_text: str, threshold: float = 0.8, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Search stored pairs by NL text similarity (keyword-based fallback).

        Uses normalized keyword overlap when embeddings are unavailable.

        Args:
            nl_text: Query NL text.
            threshold: Minimum similarity score (0.0–1.0).
            limit: Maximum results.

        Returns:
            List of matching pair dicts with added ``score`` key.
        """
        query_words = set(_normalize_nl(nl_text).split())
        if not query_words:
            return []

        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM query_pairs ORDER BY rating DESC, created_at DESC LIMIT 500"
        ).fetchall()

        scored: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            target_words = set((d.get("nl_normalized") or "").split())
            if not target_words:
                continue
            overlap = query_words & target_words
            union = query_words | target_words
            score = len(overlap) / len(union) if union else 0.0
            if score >= threshold:
                d["score"] = round(score, 4)
                scored.append(d)

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    # ── stats ──────────────────────────────────────────────────────────

    def stats(self, domain: str = "") -> dict[str, Any]:
        """Return aggregate statistics for stored query pairs."""
        domain_filter = "WHERE domain_id = ?" if domain else ""
        params: list[Any] = ([domain] if domain else [])

        conn = self._get_conn()

        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM query_pairs {domain_filter}", params
        ).fetchone()
        total = total_row["n"] if total_row else 0

        if total == 0:
            return {
                "total": 0, "avg_rating": 0.0, "with_embeddings": 0,
                "by_source": {}, "by_domain": {}, "domain_id": domain or "*",
            }

        rating_where = f"{domain_filter} AND rating > 0" if domain_filter else "WHERE rating > 0"
        avg = conn.execute(
            f"SELECT AVG(CAST(rating AS REAL)) AS a FROM query_pairs {rating_where}",
            params,
        ).fetchone()
        avg_rating = round(avg["a"], 2) if avg and avg["a"] else 0.0

        emb_where = (
            f"{domain_filter} AND embedding_json IS NOT NULL"
            if domain_filter else "WHERE embedding_json IS NOT NULL"
        )
        emb_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM query_pairs {emb_where}", params
        ).fetchone()
        with_embeddings = emb_row["n"] if emb_row else 0

        source_rows = conn.execute(
            f"SELECT source, COUNT(*) AS n FROM query_pairs {domain_filter} "
            f"GROUP BY source", params
        ).fetchall()
        by_source = {r["source"]: r["n"] for r in source_rows}

        dom_rows = conn.execute(
            "SELECT domain_id, COUNT(*) AS n FROM query_pairs GROUP BY domain_id"
        ).fetchall()
        by_domain = {r["domain_id"]: r["n"] for r in dom_rows}

        return {
            "total": total,
            "avg_rating": avg_rating,
            "with_embeddings": with_embeddings,
            "by_source": by_source,
            "by_domain": by_domain,
            "domain_id": domain or "*",
        }

    def count(self, domain: str = "") -> int:
        """Return the total number of stored pairs."""
        if domain:
            row = self._get_conn().execute(
                "SELECT COUNT(*) AS n FROM query_pairs WHERE domain_id = ?",
                (domain,),
            ).fetchone()
        else:
            row = self._get_conn().execute(
                "SELECT COUNT(*) AS n FROM query_pairs"
            ).fetchone()
        return row["n"] if row else 0

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self) -> None:
        self.close()


# ── module-level singleton ────────────────────────────────────────────────

query_pair_store = QueryPairStore()
