"""LanceDB vector store — connection management and three-namespace indexing.

See SPEC §4.2.4 and implementation-plan.md §4.8 for the full RAG architecture.

**Three namespaces** (each maps to a LanceDB table):

=============== ===================== =========================================
Namespace        Document type         Purpose
=============== ===================== =========================================
schema_metadata  :class:`SchemaDocument`  Table/column metadata for Schema linking
metrics          :class:`MetricDocument`  Business KPIs and metric definitions
documents        :class:`Document`        User docs, guides, best practices
=============== ===================== =========================================

Usage::

    store = LanceDBStore(dimension=1536)
    await store.init_namespaces()
    await store.insert("schema_metadata", docs, embeddings)
    results = await store.search("schema_metadata", query_vector, top_k=10)
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import pyarrow as pa

logger = logging.getLogger(__name__)

# ── LanceDB integration (optional — graceful degradation) ─────────────
try:
    import lancedb

    _LANCEDB_AVAILABLE = True
except ImportError:
    lancedb = None  # type: ignore[assignment]
    _LANCEDB_AVAILABLE = False

# ── Constants ──────────────────────────────────────────────────────────

DEFAULT_LANCEDB_DIR = os.path.join(
    os.path.expanduser("~"), ".data_engineer", "lancedb"
)

NAMESPACE_SCHEMA_METADATA = "schema_metadata"
NAMESPACE_METRICS = "metrics"
NAMESPACE_DOCUMENTS = "documents"
NAMESPACE_QUERY_CACHE = "query_cache"

ALL_NAMESPACES = {
    NAMESPACE_SCHEMA_METADATA,
    NAMESPACE_METRICS,
    NAMESPACE_DOCUMENTS,
    NAMESPACE_QUERY_CACHE,
}

# Name of the vector column in each table
VECTOR_COL = "vector"


# ── PyArrow schemas ────────────────────────────────────────────────────


def _make_schema_metadata_schema(dimension: int) -> pa.Schema:
    """PyArrow schema for the ``schema_metadata`` namespace."""
    return pa.schema([
        ("doc_id", pa.utf8()),
        ("db_id", pa.utf8()),
        ("schema_name", pa.utf8()),
        ("table_name", pa.utf8()),
        ("column_name", pa.utf8()),  # nullable handled at value level
        ("data_type", pa.utf8()),
        ("comment", pa.utf8()),
        ("is_primary_key", pa.bool_()),
        ("is_foreign_key", pa.bool_()),
        ("fk_references_json", pa.utf8()),  # JSON-serialised tuple
        ("enum_values_json", pa.utf8()),  # JSON-serialised list
        ("sample_values_json", pa.utf8()),
        ("table_row_count", pa.int64()),
        ("embedding_text", pa.utf8()),
        ("tags_json", pa.utf8()),  # JSON-serialised list
        ("updated_at", pa.timestamp("us", tz="UTC")),
        (VECTOR_COL, pa.list_(pa.float32(), list_size=dimension)),
    ])


def _make_metrics_schema(dimension: int) -> pa.Schema:
    """PyArrow schema for the ``metrics`` namespace."""
    return pa.schema([
        ("doc_id", pa.utf8()),
        ("domain", pa.utf8()),
        ("name", pa.utf8()),
        ("name_zh", pa.utf8()),
        ("aliases_json", pa.utf8()),
        ("description", pa.utf8()),
        ("formula", pa.utf8()),
        ("metric_type", pa.utf8()),
        ("aggregation", pa.utf8()),
        ("dimensions_json", pa.utf8()),
        ("time_grain", pa.utf8()),
        ("precision", pa.int32()),
        ("sql_template", pa.utf8()),
        ("embedding_text", pa.utf8()),
        ("updated_at", pa.timestamp("us", tz="UTC")),
        ("version", pa.int32()),
        (VECTOR_COL, pa.list_(pa.float32(), list_size=dimension)),
    ])


def _make_documents_schema(dimension: int) -> pa.Schema:
    """PyArrow schema for the ``documents`` namespace."""
    return pa.schema([
        ("doc_id", pa.utf8()),
        ("domain", pa.utf8()),
        ("title", pa.utf8()),
        ("content", pa.utf8()),
        ("content_type", pa.utf8()),
        ("source", pa.utf8()),
        ("chunk_index", pa.int32()),
        ("parent_doc_id", pa.utf8()),
        ("embedding_text", pa.utf8()),
        ("keywords_json", pa.utf8()),
        ("language", pa.utf8()),
        ("created_at", pa.timestamp("us", tz="UTC")),
        ("updated_at", pa.timestamp("us", tz="UTC")),
        (VECTOR_COL, pa.list_(pa.float32(), list_size=dimension)),
    ])


def _make_query_cache_schema(dimension: int) -> pa.Schema:
    """PyArrow schema for the ``query_cache`` namespace."""
    return pa.schema([
        ("cache_id", pa.utf8()),
        ("query", pa.utf8()),
        ("sql_text", pa.utf8()),
        ("confidence", pa.float64()),
        ("domain", pa.utf8()),
        ("created_at", pa.timestamp("us", tz="UTC")),
        (VECTOR_COL, pa.list_(pa.float32(), list_size=dimension)),
    ])


# ── LanceDBStore ───────────────────────────────────────────────────────


class LanceDBStore:
    """Manages a local LanceDB instance with three pre-defined namespaces.

    Args:
        dimension: Embedding vector dimension (e.g. 1536 for OpenAI, 1024 for BGE).
        uri: Path to the LanceDB data directory. Defaults to
            ``~/.data_engineer/lancedb``.
    """

    def __init__(self, dimension: int, uri: str | None = None):
        if not _LANCEDB_AVAILABLE:
            raise ImportError(
                "LanceDBStore requires the 'lancedb' package. "
                "Install it with: pip install lancedb"
            )
        self._dimension = dimension
        self._uri = uri or DEFAULT_LANCEDB_DIR
        self._db: Any = None

    # ── Connection management ──────────────────────────────────────────

    @property
    def uri(self) -> str:
        return self._uri

    @property
    def dimension(self) -> int:
        return self._dimension

    def connect(self) -> Any:
        """Open (or create) the LanceDB database.

        Returns:
            The LanceDB DB object (idempotent — returns cached connection
            if already connected).
        """
        if self._db is not None:
            return self._db
        Path(self._uri).mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(self._uri)
        return self._db

    def close(self) -> None:
        """Release the database reference (LanceDB has no explicit close)."""
        self._db = None

    def _ensure_connected(self) -> Any:
        """Connect if needed and return the DB handle."""
        if self._db is None:
            return self.connect()
        return self._db

    # ── Namespace initialisation ───────────────────────────────────────

    def init_namespaces(self, overwrite: bool = False) -> dict[str, Any]:
        """Create (or open) all three namespace tables.

        Args:
            overwrite: If ``True``, drop and recreate existing tables.

        Returns:
            Dict mapping namespace name → LanceDB table handle.
        """
        db = self._ensure_connected()

        schemas = {
            NAMESPACE_SCHEMA_METADATA: _make_schema_metadata_schema(self._dimension),
            NAMESPACE_METRICS: _make_metrics_schema(self._dimension),
            NAMESPACE_DOCUMENTS: _make_documents_schema(self._dimension),
            NAMESPACE_QUERY_CACHE: _make_query_cache_schema(self._dimension),
        }

        tables: dict[str, Any] = {}
        for namespace, schema in schemas.items():
            if overwrite:
                self._drop_table_safe(db, namespace)
            tables[namespace] = self._open_or_create(namespace, schema)
        return tables

    @staticmethod
    def _table_vector_dim(table: Any) -> int | None:
        """Read the fixed list size of the ``vector`` column, if present."""
        try:
            field = table.schema.field(VECTOR_COL)
            list_size = getattr(field.type, "list_size", None)
            return int(list_size) if list_size is not None else None
        except Exception:  # noqa: BLE001 — schema introspection failure
            return None

    @staticmethod
    def _drop_table_safe(db: Any, name: str) -> None:
        """Drop a table if it exists, swallow errors otherwise."""
        with contextlib.suppress(Exception):
            db.drop_table(name)

    def _open_or_create(self, namespace: str, schema: pa.Schema) -> Any:
        """Open *namespace*, recreating it when the vector dimension differs.

        Embedding model switches (e.g. mock 256 → ollama 768) leave stale
        vectors behind; a fixed-size list column cannot hold vectors of a
        different length, so the table must be rebuilt and reindexed.
        """
        db = self._ensure_connected()
        try:
            table = db.open_table(namespace)
        except Exception:
            return db.create_table(namespace, schema=schema, mode="create")

        existing_dim = self._table_vector_dim(table)
        if existing_dim is not None and existing_dim != self._dimension:
            logger.warning(
                "LanceDB table '%s' dimension mismatch "
                "(existing=%s, expected=%s) — dropping and recreating; "
                "indexes will be rebuilt.",
                namespace, existing_dim, self._dimension,
            )
            self._drop_table_safe(db, namespace)
            return db.create_table(namespace, schema=schema, mode="create")
        return table

    def ensure_namespace(self, namespace: str) -> Any:
        """Ensure a single namespace table exists, creating it if necessary.

        Args:
            namespace: One of the three namespace names.

        Returns:
            The LanceDB table handle.

        Raises:
            ValueError: If *namespace* is not a recognised name.
        """
        if namespace not in ALL_NAMESPACES:
            raise ValueError(
                f"Unknown namespace '{namespace}'. "
                f"Expected one of: {ALL_NAMESPACES}"
            )
        schemas = {
            NAMESPACE_SCHEMA_METADATA: _make_schema_metadata_schema,
            NAMESPACE_METRICS: _make_metrics_schema,
            NAMESPACE_DOCUMENTS: _make_documents_schema,
            NAMESPACE_QUERY_CACHE: _make_query_cache_schema,
        }
        return self._open_or_create(namespace, schemas[namespace](self._dimension))

    # ── CRUD operations ────────────────────────────────────────────────

    def insert(
        self,
        namespace: str,
        records: list[dict[str, Any]],
    ) -> int:
        """Insert (or upsert) records into a namespace.

        Each record dict must contain all columns defined by the namespace
        schema, including ``"vector"`` (list of floats of length *dimension*).

        Args:
            namespace: Target namespace.
            records: List of record dicts matching the namespace schema.

        Returns:
            Number of records inserted.
        """
        if not records:
            return 0
        table = self.ensure_namespace(namespace)
        table.add(records)
        return len(records)

    def search(
        self,
        namespace: str,
        vector: list[float],
        top_k: int = 10,
        *,
        filter_expr: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search a namespace by vector similarity (cosine distance).

        Args:
            namespace: Namespace to search.
            vector: Query embedding vector.
            top_k: Maximum number of results to return.
            filter_expr: Optional LanceDB filter expression (SQL-like),
                e.g. ``"db_id = 'mydb'"``.

        Returns:
            List of result dicts, each including a ``_distance`` key with
            the cosine distance (lower = more similar).
        """
        table = self.ensure_namespace(namespace)
        query = table.search(vector).metric("cosine").limit(top_k)
        if filter_expr:
            query = query.where(filter_expr)
        return query.to_list()

    def delete(self, namespace: str, where: str) -> int:
        """Delete records from a namespace matching a filter expression.

        Args:
            namespace: Target namespace.
            where: SQL-like filter, e.g. ``"doc_id = 'abc123'"``.

        Returns:
            Number of records deleted.
        """
        table = self.ensure_namespace(namespace)
        count_before = table.count_rows()
        table.delete(where)
        count_after = table.count_rows()
        return count_before - count_after

    def count(self, namespace: str) -> int:
        """Return the number of rows in a namespace."""
        table = self.ensure_namespace(namespace)
        return table.count_rows()

    def drop_namespace(self, namespace: str) -> None:
        """Drop a namespace table entirely."""
        if namespace not in ALL_NAMESPACES:
            raise ValueError(f"Unknown namespace '{namespace}'.")
        db = self._ensure_connected()
        self._drop_table_safe(db, namespace)

    # ── Convenience ────────────────────────────────────────────────────

    def list_namespaces(self) -> list[str]:
        """Return names of all three namespace tables that currently exist."""
        db = self._ensure_connected()
        existing: list[str] = []
        for ns in ALL_NAMESPACES:
            try:
                db.open_table(ns)
                existing.append(ns)
            except Exception:
                pass
        return existing

    def namespace_stats(self) -> dict[str, int]:
        """Return row counts for all existing namespaces."""
        return {ns: self.count(ns) for ns in self.list_namespaces()}


# ── Helper: document → record conversion ───────────────────────────────


def schema_doc_to_record(
    doc: Any, vector: list[float]
) -> dict[str, Any]:
    """Convert a :class:`~app.models.rag_schema.SchemaDocument` to a LanceDB record dict.

    Args:
        doc: The SchemaDocument instance.
        vector: The pre-computed embedding vector.

    Returns:
        Dict ready for :meth:`LanceDBStore.insert`.
    """
    return {
        "doc_id": doc.doc_id,
        "db_id": doc.db_id,
        "schema_name": doc.schema_name,
        "table_name": doc.table_name,
        "column_name": doc.column_name or "",
        "data_type": doc.data_type or "",
        "comment": doc.comment or "",
        "is_primary_key": doc.is_primary_key,
        "is_foreign_key": doc.is_foreign_key,
        "fk_references_json": json.dumps(list(doc.fk_references) if doc.fk_references else []),
        "enum_values_json": json.dumps(doc.enum_values or []),
        "sample_values_json": json.dumps([str(v) for v in doc.sample_values]),
        "table_row_count": doc.table_row_count,
        "embedding_text": doc.embedding_text,
        "tags_json": json.dumps(doc.tags),
        "updated_at": doc.updated_at,
        VECTOR_COL: [float(v) for v in vector],
    }


def metric_doc_to_record(
    doc: Any, vector: list[float]
) -> dict[str, Any]:
    """Convert a :class:`~app.models.rag_metric.MetricDocument` to a LanceDB record dict."""
    return {
        "doc_id": doc.doc_id,
        "domain": doc.domain,
        "name": doc.name,
        "name_zh": doc.name_zh,
        "aliases_json": json.dumps(doc.aliases),
        "description": doc.description,
        "formula": doc.formula,
        "metric_type": doc.metric_type,
        "aggregation": doc.aggregation,
        "dimensions_json": json.dumps(doc.dimensions),
        "time_grain": doc.time_grain,
        "precision": doc.precision,
        "sql_template": doc.sql_template,
        "embedding_text": doc.embedding_text,
        "updated_at": doc.updated_at,
        "version": doc.version,
        VECTOR_COL: [float(v) for v in vector],
    }


def document_to_record(
    doc: Any, vector: list[float]
) -> dict[str, Any]:
    """Convert a :class:`~app.models.rag_document.Document` to a LanceDB record dict."""
    return {
        "doc_id": doc.doc_id,
        "domain": doc.domain or "",
        "title": doc.title,
        "content": doc.content,
        "content_type": doc.content_type,
        "source": doc.source,
        "chunk_index": doc.chunk_index,
        "parent_doc_id": doc.parent_doc_id or "",
        "embedding_text": doc.embedding_text,
        "keywords_json": json.dumps(doc.keywords),
        "language": doc.language,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        VECTOR_COL: [float(v) for v in vector],
    }
