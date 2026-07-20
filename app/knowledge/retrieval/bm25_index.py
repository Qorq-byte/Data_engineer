"""BM25 sparse retrieval index — SQLite FTS5 backend with jieba/whitespace tokenization.

See SPEC §4.2.4 and implementation-plan.md §4.8.3 for the full RAG architecture.

**Two-tier tokenizer:**

- **Chinese (zh)**: jieba exact-mode segmentation → stopword + single-char filtering
- **English (en)**: whitespace split → lowercase → punctuation strip → stopword filtering
- **Mixed**: both pipelines, deduplicated (zh-first order)

**BM25 parameters** (per implementation-plan §4.8.3): k1=1.5, b=0.75.

Usage::

    index = BM25Index(":memory:")  # or file path
    index.index("my_ns", [{"doc_id": "d1", "text": "hello world"}])
    results = index.search("my_ns", "hello query", top_k=10)
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

# ── Chinese tokenizer (optional — graceful degradation) ────────────────
try:
    import jieba

    _JIEBA_AVAILABLE = True
except ImportError:
    jieba = None  # type: ignore[assignment]
    _JIEBA_AVAILABLE = False


# ── Constants ──────────────────────────────────────────────────────────

# FTS5 special characters that need escaping in MATCH queries
_FTS5_SPECIAL_RE = re.compile(r"([+\-*()~\"^:])")

# English punctuation / non-word characters to strip during tokenization
_EN_PUNCT_RE = re.compile(r"[^\w\s]")

# CJK Unicode ranges (same as LanguageDetector)
_CJK_RANGES: list[tuple[int, int]] = [
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
]

# Chinese stopwords — high-frequency function words with low retrieval value
_ZH_STOPWORDS: set[str] = {
    "的", "了", "在", "是", "我", "有", "和", "就",
    "不", "人", "都", "一", "一个", "上", "也", "很",
    "到", "说", "要", "去", "你", "会", "着", "没有",
    "看", "好", "自己", "这", "他", "她", "它", "们",
    "那", "些", "什么", "怎么", "如何", "为什么",
    "吗", "呢", "吧", "啊", "哎", "哦", "嗎",
    "能", "可以", "应该", "必须", "需要",
    "所有", "每个", "还是", "或者", "然后",
    "因为", "所以", "但是", "如果", "虽然",
    "已经", "正在", "将", "被", "把",
    "来", "做", "搞", "弄",
}

# English stopwords — high-frequency function words
_EN_STOPWORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "and", "or", "not",
    "no", "but", "if", "then", "else", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "only", "own", "same", "so", "than", "too", "very",
    "just", "about", "also", "it", "its", "he", "she", "they", "them",
    "we", "us", "you", "me", "my", "your", "our", "their", "his", "her",
    "this", "that", "these", "those", "what", "which", "who", "whom",
}

# FTS5 keyword operators (case-insensitive) — must quote these if they appear as tokens
_FTS5_OPERATORS: frozenset[str] = frozenset({"OR", "AND", "NOT"})

# Default BM25 parameters
BM25_K1 = 1.5
BM25_B = 0.75


# ── Helpers ────────────────────────────────────────────────────────────


def _is_cjk_char(ch: str) -> bool:
    """Check whether a single character falls within any CJK Unicode range."""
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _detect_language(text: str) -> str:
    """Quick CJK ratio language detection (same algorithm as LanguageDetector)."""
    if not text:
        return "en"
    cjk_count = 0
    alpha_count = 0
    for ch in text:
        if _is_cjk_char(ch):
            cjk_count += 1
        elif ch.isalpha():
            alpha_count += 1
    total = cjk_count + alpha_count
    if total == 0:
        return "en"
    ratio = cjk_count / total
    if ratio > 0.5:
        return "zh"
    if ratio > 0.1:
        return "mixed"
    return "en"


def _escape_fts5_token(token: str) -> str:
    """Escape FTS5 special characters in a token, quote if it's a reserved operator."""
    escaped = _FTS5_SPECIAL_RE.sub(r"\\\1", token)
    if escaped.upper() in _FTS5_OPERATORS:
        return f'"{escaped}"'
    return escaped


# ── BM25Tokenizer ──────────────────────────────────────────────────────


class BM25Tokenizer:
    """Two-tier tokenizer: jieba (Chinese) + whitespace (English).

    All methods are static — the tokenizer is stateless.
    """

    @staticmethod
    def tokenize(text: str, language: str = "auto") -> list[str]:
        """Tokenize *text* according to *language*.

        Args:
            text: Raw input text.
            language: ``"zh"``, ``"en"``, ``"mixed"``, or ``"auto"``
                (auto-detect via CJK character ratio).

        Returns:
            List of lowercased, filtered token strings.
        """
        if not text:
            return []

        if language == "auto":
            language = _detect_language(text)

        if language == "zh":
            return BM25Tokenizer._tokenize_zh(text)
        elif language == "mixed":
            zh_tokens = BM25Tokenizer._tokenize_zh(text)
            en_tokens = BM25Tokenizer._tokenize_en(text)
            seen: set[str] = set(zh_tokens)
            result: list[str] = list(zh_tokens)
            for t in en_tokens:
                if t not in seen:
                    seen.add(t)
                    result.append(t)
            return result
        else:
            return BM25Tokenizer._tokenize_en(text)

    @staticmethod
    def _tokenize_zh(text: str) -> list[str]:
        """Chinese tokenization via jieba exact mode, with stopword + single-char filtering.

        Falls back to character bigrams if jieba is unavailable.
        """
        if not _JIEBA_AVAILABLE:
            # Character bigram fallback — usable but lower quality
            clean = re.sub(r"[^一-鿿]", "", text)
            tokens: list[str] = []
            for i in range(len(clean)):
                ch = clean[i]
                if ch not in _ZH_STOPWORDS:
                    tokens.append(ch)
                if i < len(clean) - 1:
                    bigram = clean[i : i + 2]
                    if bigram not in _ZH_STOPWORDS:
                        tokens.append(bigram)
            return tokens

        raw = jieba.lcut(text)
        return [
            t.strip().lower()
            for t in raw
            if t.strip()
            and t.strip() not in _ZH_STOPWORDS
            and len(t.strip()) >= 2
        ]

    @staticmethod
    def _tokenize_en(text: str) -> list[str]:
        """English tokenization: strip punctuation, lowercase, split, filter stopwords."""
        clean = _EN_PUNCT_RE.sub(" ", text)
        tokens = clean.lower().split()
        return [t for t in tokens if t not in _EN_STOPWORDS]

    @staticmethod
    def tokenize_for_index(text: str, language: str = "auto") -> str:
        """Tokenize *text* and join with spaces — ready for storage in FTS5 text column.

        This pre-tokenization is critical for Chinese: FTS5's unicode61 tokenizer
        treats each CJK character as a separate token, but jieba segments at the
        word level.  By pre-segmenting and joining with spaces, each jieba token
        becomes one FTS5 token.
        """
        tokens = BM25Tokenizer.tokenize(text, language)
        return " ".join(tokens)

    @staticmethod
    def build_fts5_query(query_text: str, language: str = "auto") -> str:
        """Build an FTS5 MATCH query string with OR-joined tokens.

        Tokens are escaped for FTS5 special characters and double-quoted so
        each token is treated as an exact term match.

        Args:
            query_text: Raw user query string.
            language: Language code for tokenization.

        Returns:
            FTS5 query string, e.g. ``"\"sales\" OR \"revenue\" OR \"monthly\""``.
        """
        tokens = BM25Tokenizer.tokenize(query_text, language)
        if not tokens:
            return '""'
        escaped = [_escape_fts5_token(t) for t in tokens]
        return " OR ".join(f'"{t}"' for t in escaped)


# ── BM25Index ──────────────────────────────────────────────────────────


class BM25Index:
    """BM25 sparse retrieval index backed by SQLite FTS5.

    Each namespace maps to one FTS5 virtual table (``fts5_{namespace}``).

    Args:
        db_path: SQLite database path.  Use ``":memory:"`` for testing or
            a file path for persistence.

    BM25 parameters:  k1 = 1.5,  b = 0.75  (per implementation-plan §4.8.3).
    """

    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def db_path(self) -> str:
        return self._db_path

    # ── Connection management ──────────────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        """Open (or create) the SQLite database.  Idempotent."""
        if self._conn is not None:
            return self._conn
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            # Fallback: try DELETE mode if WAL fails (e.g., locked files)
            try:
                self._conn.execute("PRAGMA journal_mode=DELETE")
            except sqlite3.OperationalError:
                pass
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _ensure_connected(self) -> sqlite3.Connection:
        if self._conn is None:
            return self.connect()
        return self._conn

    def _table_name(self, namespace: str) -> str:
        """FTS5 virtual table name for a namespace."""
        # Sanitise namespace to prevent SQL injection via table name
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", namespace):
            raise ValueError(
                f"Invalid namespace name: {namespace!r}. "
                f"Must be a valid SQL identifier (alphanumeric + underscore)."
            )
        return f"fts5_{namespace}"

    # ── Namespace management ───────────────────────────────────────────

    def ensure_namespace(self, namespace: str) -> None:
        """Create the FTS5 virtual table for *namespace* if it doesn't exist.

        Table columns: ``doc_id``, ``text``, ``metadata_json``.
        Tokenizer: ``unicode61`` (case-folding + diacritic removal).
        """
        conn = self._ensure_connected()
        table = self._table_name(namespace)
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING fts5("
            "doc_id, text, metadata_json, tokenize='unicode61')"
        )
        conn.commit()

    def drop_namespace(self, namespace: str) -> None:
        """Drop the FTS5 virtual table for *namespace*."""
        conn = self._ensure_connected()
        table = self._table_name(namespace)
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()

    # ── CRUD ───────────────────────────────────────────────────────────

    def index(
        self,
        namespace: str,
        docs: list[dict[str, Any]],
        language: str = "auto",
    ) -> int:
        """Index (or re-index) a batch of documents.

        Each doc dict must have:
            - ``doc_id`` (str): Unique document identifier.
            - ``text`` (str): Raw text content to index.
            - ``metadata`` (dict, optional): Arbitrary JSON-serialisable metadata.

        Documents with the same ``doc_id`` are replaced (DELETE + INSERT).

        Args:
            namespace: Target namespace.
            docs: List of document dicts.
            language: Language hint for tokenization.

        Returns:
            Number of documents indexed.
        """
        if not docs:
            return 0

        self.ensure_namespace(namespace)
        conn = self._ensure_connected()
        table = self._table_name(namespace)

        rows: list[tuple[str, str, str]] = []
        for doc in docs:
            doc_id = doc["doc_id"]
            raw_text = doc.get("text", "")
            metadata = doc.get("metadata", {})
            tokenized = BM25Tokenizer.tokenize_for_index(raw_text, language)
            rows.append((doc_id, tokenized, json.dumps(metadata, ensure_ascii=False)))

        # Delete existing rows with same doc_ids, then insert (avoids rowid conflicts)
        doc_ids = [(doc["doc_id"],) for doc in docs]
        conn.executemany(f"DELETE FROM {table} WHERE doc_id = ?", doc_ids)
        conn.executemany(
            f"INSERT INTO {table}(doc_id, text, metadata_json) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
        return len(rows)

    def search(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        language: str = "auto",
    ) -> list[dict[str, Any]]:
        """BM25 search over a namespace.

        Args:
            namespace: Namespace to search.
            query: Raw query text (tokenized internally).
            top_k: Maximum number of results.
            language: Language hint for query tokenization.

        Returns:
            List of result dicts, each with:
                - ``doc_id`` (str)
                - ``text`` (str): Tokenized text that was indexed.
                - ``score`` (float): Positive BM25 score (higher = better match).
                - ``metadata`` (dict): Stored metadata.
        """
        self.ensure_namespace(namespace)
        conn = self._ensure_connected()
        table = self._table_name(namespace)

        fts5_query = BM25Tokenizer.build_fts5_query(query, language)
        if not fts5_query or fts5_query == '""':
            return []

        try:
            rows = conn.execute(
                f"SELECT doc_id, text, metadata_json, rank FROM {table} "
                f"WHERE {table} MATCH ? ORDER BY rank LIMIT ?",
                (fts5_query, top_k),
            ).fetchall()
        except sqlite3.OperationalError:
            # Malformed query → no results
            return []

        results: list[dict[str, Any]] = []
        for row in rows:
            result: dict[str, Any] = {
                "doc_id": row["doc_id"],
                "text": row["text"],
                "score": -row["rank"],  # FTS5 rank is negative BM25; negate for intuition
            }
            try:
                result["metadata"] = json.loads(row["metadata_json"])
            except (json.JSONDecodeError, TypeError):
                result["metadata"] = {}
            results.append(result)

        return results

    def delete(self, namespace: str, doc_id: str) -> int:
        """Delete a single document by ``doc_id``.

        Returns:
            Number of rows deleted (0 or 1).
        """
        self.ensure_namespace(namespace)
        conn = self._ensure_connected()
        table = self._table_name(namespace)
        cur = conn.execute(f"DELETE FROM {table} WHERE doc_id = ?", (doc_id,))
        conn.commit()
        return cur.rowcount

    def delete_by_field(self, namespace: str, field: str, value: str) -> int:
        """Delete rows where ``metadata_json.{field}`` matches *value*.

        Uses SQLite ``json_extract()`` on the ``metadata_json`` TEXT column.
        Useful for bulk cleanup::

            bm25.delete_by_field("metrics", "domain", "ecommerce")

        Args:
            namespace: Target namespace.
            field: JSON key inside ``metadata_json``.
            value: String value to match.

        Returns:
            Number of rows deleted (best-effort; may be 0 if table doesn't exist).
        """
        self.ensure_namespace(namespace)
        conn = self._ensure_connected()
        table = self._table_name(namespace)
        try:
            cur = conn.execute(
                f"DELETE FROM {table} WHERE json_extract(metadata_json, '$.{field}') = ?",
                (value,),
            )
            conn.commit()
            return cur.rowcount
        except sqlite3.OperationalError:
            return 0

    def clear(self, namespace: str) -> None:
        """Remove all documents from *namespace* (drop + recreate the FTS5 table)."""
        self.drop_namespace(namespace)
        self.ensure_namespace(namespace)

    # ── Introspection ──────────────────────────────────────────────────

    def count(self, namespace: str) -> int:
        """Return the number of indexed documents in *namespace*."""
        try:
            conn = self._ensure_connected()
            table = self._table_name(namespace)
            row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()
            return row["cnt"] if row else 0
        except sqlite3.OperationalError:
            return 0

    def list_namespaces(self) -> list[str]:
        """Return names of all FTS5 namespaces currently in the database."""
        conn = self._ensure_connected()
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'fts5_%'"
        ).fetchall()
        return [row["name"].replace("fts5_", "", 1) for row in rows]
