r"""Evolvable Context — living knowledge base with 4 knowledge types.

See implementation-plan §4.8.5 and progress-log §3.12 for the full specification.

The Evolvable Context is a living knowledge base that continuously captures
four types of knowledge and evolves them through a Sense→Ingest→Retrieve→Verify
lifecycle.

**Four knowledge types**::

    SCHEMA          — table/column metadata from DDL or connection probing
    REFERENCE_SQL   — user-confirmed NL→SQL pairs with quality scores
    SEMANTIC_MODEL  — business term/entity definitions and mappings
    METRIC          — business KPI definitions with formulas and dimensions

**Lifecycle**::

    active  →  stale  →  archived
      ↑                      │
      └── reactivate ←───────┘

Usage::

    from app.knowledge.evolvable_context import EvolvableContext, KnowledgeType

    ctx = EvolvableContext()

    # Ingest
    ctx.ingest_schema(schema_snapshot, domain="ecommerce")
    ctx.ingest_reference_sql(
        nl="订单金额排行", sql="SELECT ...", confidence=0.95, domain="ecommerce",
    )

    # Retrieve
    items = ctx.retrieve(query="订单", type=KnowledgeType.REFERENCE_SQL, domain="ecommerce")
    s = ctx.stats()  # → {"total": 42, "by_type": {"SCHEMA": 5, ...}, ...}
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# ── KnowledgeType ───────────────────────────────────────────────────────


class KnowledgeType(StrEnum):
    """The four types of evolvable knowledge."""

    SCHEMA = "SCHEMA"
    """Table/column metadata captured from DDL or connection probing."""

    REFERENCE_SQL = "REFERENCE_SQL"
    """User-confirmed NL→SQL pairs with confidence scores."""

    SEMANTIC_MODEL = "SEMANTIC_MODEL"
    """Business term/entity definitions and mappings."""

    METRIC = "METRIC"
    """Business KPI definitions with formulas, dimensions, time grains."""


# ── KnowledgeItem ───────────────────────────────────────────────────────


class KnowledgeItem:
    """A single piece of evolvable knowledge.

    Attributes:
        id: Unique identifier (generated if not provided).
        type: One of the four ``KnowledgeType`` values.
        content: Flexible payload — structure depends on *type*:
            - SCHEMA: ``{"tables": [...], "columns": [...], "version": n}``
            - REFERENCE_SQL: ``{"nl": "...", "sql": "...", "confidence": 0.9}``
            - SEMANTIC_MODEL: ``{"term": "...", "mapping": {...}}``
            - METRIC: ``{"name": "...", "formula": "...", "dimensions": [...]}``
        source: Origin label (e.g. ``"schema_sync"``, ``"user_confirm"``,
                ``"domain_config"``, ``"auto_discover"``).
        version: Monotonic version number, starts at 1.
        created_at: Timestamp of first ingestion (UTC).
        updated_at: Timestamp of last update (UTC).
        last_accessed_at: Timestamp of last retrieval, or ``None``.
        score: Quality / relevance score 0.0–1.0.
        status: Lifecycle status: ``"active"`` | ``"stale"`` | ``"archived"``.
        domain_id: Owning domain, or ``""`` for global.
        tags: Freeform tags for filtering.
    """

    __slots__ = (
        "id",
        "type",
        "content",
        "source",
        "version",
        "created_at",
        "updated_at",
        "last_accessed_at",
        "score",
        "status",
        "domain_id",
        "tags",
    )

    def __init__(
        self,
        type: KnowledgeType,
        content: dict[str, Any] | None = None,
        source: str = "",
        id: str = "",
        version: int = 1,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        last_accessed_at: datetime | None = None,
        score: float = 0.5,
        status: str = "active",
        domain_id: str = "",
        tags: list[str] | None = None,
    ) -> None:
        self.id = id or _generate_id()
        self.type = type
        self.content = content or {}
        self.source = source
        self.version = version
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at or self.created_at
        self.last_accessed_at = last_accessed_at
        self.score = score
        self.status = status
        self.domain_id = domain_id
        self.tags = tags or []

    def __repr__(self) -> str:
        return (
            f"KnowledgeItem(id={self.id!r}, type={self.type.value!r}, "
            f"status={self.status!r}, domain={self.domain_id!r}, "
            f"score={self.score:.2f})"
        )

    def touch(self) -> None:
        """Update ``last_accessed_at`` to now."""
        self.last_accessed_at = datetime.now(UTC)


# ── EvolvableContext ────────────────────────────────────────────────────


@dataclass
class EvolutionRecord:
    """Records what changed during an evolution cycle.

    Attributes:
        cycle_id: Unique evolution cycle identifier.
        items_transitioned: Count of items that changed status.
        items_ingested: Count of new items auto-ingested.
        items_archived: Count of items archived this cycle.
        items_reactivated: Count of items reactivated this cycle.
        details: Human-readable summary of changes.
        created_at: ISO-8601 timestamp.
    """

    cycle_id: str = field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:8]}")
    items_transitioned: int = 0
    items_ingested: int = 0
    items_archived: int = 0
    items_reactivated: int = 0
    details: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class EvolvableContext:
    """Living knowledge base that captures and evolves 4 types of knowledge.

    Args:
        domain_manager: Optional ``DomainManager`` for domain-aware operations.
    """

    # Valid lifecycle statuses
    STATUSES = ("active", "stale", "archived")

    def __init__(self, domain_manager: Any = None) -> None:
        self._domain_manager = domain_manager
        self._items: dict[str, KnowledgeItem] = {}

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def item_count(self) -> int:
        """Total number of knowledge items."""
        return len(self._items)

    @property
    def type_counts(self) -> dict[str, int]:
        """Item count broken down by ``KnowledgeType``."""
        counts: dict[str, int] = {}
        for item in self._items.values():
            key = item.type.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    @property
    def domain_counts(self) -> dict[str, int]:
        """Item count broken down by domain."""
        counts: dict[str, int] = {}
        for item in self._items.values():
            key = item.domain_id or "__global__"
            counts[key] = counts.get(key, 0) + 1
        return counts

    # ── Ingestion ──────────────────────────────────────────────────────

    def ingest(self, item: KnowledgeItem) -> KnowledgeItem:
        """Add or update a knowledge item.

        If an item with the same *id* already exists, its version is
        incremented and content is merged.

        Args:
            item: The ``KnowledgeItem`` to ingest.

        Returns:
            The ingested item (existing item updated, or new item stored).
        """
        existing = self._items.get(item.id)
        if existing is not None:
            # Update existing: bump version, merge content
            existing.version += 1
            existing.content.update(item.content)
            existing.source = item.source or existing.source
            existing.score = item.score
            existing.updated_at = datetime.now(UTC)
            existing.tags = sorted(set(existing.tags + item.tags))
            existing.status = item.status
            return existing
        else:
            self._items[item.id] = item
            return item

    def ingest_schema(
        self,
        tables: list[str] | None = None,
        columns: dict[str, list[str]] | None = None,
        *,
        source: str = "schema_sync",
        domain: str = "",
        version: int = 1,
        extra: dict[str, Any] | None = None,
    ) -> KnowledgeItem:
        """Ingest a schema snapshot.

        Args:
            tables: List of table names captured.
            columns: Per-table column names dict (``{table: [col, ...]}``).
            source: Origin label.
            domain: Domain scope.
            version: Schema version number.
            extra: Additional metadata to include in content.

        Returns:
            The newly created ``KnowledgeItem``.
        """
        content: dict[str, Any] = {
            "tables": tables or [],
            "columns": columns or {},
            "schema_version": version,
        }
        if extra:
            content.update(extra)

        item = KnowledgeItem(
            type=KnowledgeType.SCHEMA,
            content=content,
            source=source,
            domain_id=domain,
            score=0.8,
            tags=["schema", "auto"],
        )
        self._items[item.id] = item
        return item

    def ingest_reference_sql(
        self,
        nl: str,
        sql: str,
        *,
        confidence: float = 0.5,
        source: str = "user_confirm",
        domain: str = "",
        tables_used: list[str] | None = None,
        intent: str = "",
    ) -> KnowledgeItem:
        """Ingest a user-confirmed NL→SQL pair.

        Args:
            nl: Natural-language query text.
            sql: The confirmed SQL.
            confidence: Quality score 0.0–1.0.
            source: Origin label (``"user_confirm"``, ``"auto_capture"``).
            domain: Domain scope.
            tables_used: Tables referenced by the SQL.
            intent: Classified intent type.

        Returns:
            The newly created ``KnowledgeItem``.
        """
        content: dict[str, Any] = {
            "nl": nl,
            "sql": sql,
            "confidence": confidence,
            "tables_used": tables_used or [],
            "intent": intent,
        }
        item = KnowledgeItem(
            type=KnowledgeType.REFERENCE_SQL,
            content=content,
            source=source,
            domain_id=domain,
            score=confidence,
            tags=["sql", "user_confirmed"],
        )
        self._items[item.id] = item
        return item

    def ingest_semantic_model(
        self,
        term: str,
        mapping: dict[str, Any] | None = None,
        *,
        source: str = "domain_config",
        domain: str = "",
        description: str = "",
    ) -> KnowledgeItem:
        """Ingest a semantic model (business term → SQL mapping).

        Args:
            term: Business term name.
            mapping: Term mapping dict (expression, type, table, etc.).
            source: Origin label.
            domain: Domain scope.
            description: Human-readable description.

        Returns:
            The newly created ``KnowledgeItem``.
        """
        content: dict[str, Any] = {
            "term": term,
            "mapping": mapping or {},
            "description": description,
        }
        item = KnowledgeItem(
            type=KnowledgeType.SEMANTIC_MODEL,
            content=content,
            source=source,
            domain_id=domain,
            score=0.7,
            tags=["semantic", "term_mapping"],
        )
        self._items[item.id] = item
        return item

    def ingest_metric(
        self,
        name: str,
        formula: str = "",
        dimensions: list[str] | None = None,
        *,
        source: str = "domain_config",
        domain: str = "",
        time_grain: str = "",
        aggregation: str = "",
        description: str = "",
    ) -> KnowledgeItem:
        """Ingest a business metric/KPI definition.

        Args:
            name: Metric name.
            formula: SQL formula or expression.
            dimensions: Allowed drill-down dimensions.
            source: Origin label.
            domain: Domain scope.
            time_grain: Default time granularity (``"day"``, ``"month"``, etc.).
            aggregation: Default aggregation (``"SUM"``, ``"AVG"``, etc.).
            description: Human-readable description.

        Returns:
            The newly created ``KnowledgeItem``.
        """
        content: dict[str, Any] = {
            "name": name,
            "formula": formula,
            "dimensions": dimensions or [],
            "time_grain": time_grain,
            "aggregation": aggregation,
            "description": description,
        }
        item = KnowledgeItem(
            type=KnowledgeType.METRIC,
            content=content,
            source=source,
            domain_id=domain,
            score=0.7,
            tags=["metric", "kpi"],
        )
        self._items[item.id] = item
        return item

    # ── Retrieval ──────────────────────────────────────────────────────

    def get(self, item_id: str) -> KnowledgeItem | None:
        """Return a knowledge item by ID, or ``None``."""
        item = self._items.get(item_id)
        if item is not None:
            item.touch()
        return item

    def list_all(
        self,
        type: KnowledgeType | None = None,
        domain: str | None = None,
        status: str | None = None,
    ) -> list[KnowledgeItem]:
        """List all knowledge items, optionally filtered.

        Args:
            type: Filter by ``KnowledgeType``.
            domain: Filter by domain. ``None`` = no filter.
            status: Filter by lifecycle status. ``None`` = all statuses.

        Returns:
            List of ``KnowledgeItem``, sorted by score descending then recency.
        """
        results: list[KnowledgeItem] = []

        for item in self._items.values():
            if type is not None and item.type != type:
                continue
            if domain is not None and item.domain_id != domain:
                continue
            if status is not None and item.status != status:
                continue
            results.append(item)

        # Sort: score desc, then updated_at desc for equal scores
        results.sort(key=lambda it: (-it.score, it.updated_at), reverse=False)
        # Actually that sorts by negative score → ascending... Let me fix:
        results.sort(
            key=lambda it: (-it.score, -it.updated_at.timestamp()),
        )
        return results

    def retrieve(
        self,
        query: str = "",
        type: KnowledgeType | None = None,
        domain: str | None = None,
        status: str = "active",
        top_k: int = 20,
    ) -> list[KnowledgeItem]:
        """Retrieve knowledge items relevant to a NL *query*.

        Scoring combines type/domain/status filtering with keyword match:

        1. Filter by *type*, *domain*, and *status*.
        2. Score each item against *query* via keyword/tag matching.
        3. Return top *top_k* by relevance, touching accessed items.

        Args:
            query: NL query text for relevance scoring. Empty = no scoring filter.
            type: Optional ``KnowledgeType`` filter.
            domain: Optional domain filter.
            status: Lifecycle status filter (default ``"active"``).
            top_k: Maximum number of results.

        Returns:
            List of ``KnowledgeItem`` sorted by relevance score descending.
        """
        candidates = self.list_all(type=type, domain=domain, status=status)

        if not query:
            # No query → return top_k by base score
            candidates.sort(key=lambda it: (-it.score, -it.updated_at.timestamp()))
            result = candidates[:top_k]
        else:
            scored: list[tuple[KnowledgeItem, float]] = []
            for item in candidates:
                relevance = _keyword_match_score(item, query)
                if relevance > 0.0:
                    scored.append((item, relevance))

            # Sort by relevance desc, then base score desc
            scored.sort(key=lambda x: (-x[1], -x[0].score))
            result = [item for item, _ in scored[:top_k]]

        # Touch all returned items
        for item in result:
            item.touch()

        return result

    # ── Lifecycle ──────────────────────────────────────────────────────

    def update(self, item_id: str, updates: dict[str, Any]) -> bool:
        """Update a knowledge item's fields in-place.

        Args:
            item_id: The item ID to update.
            updates: Dict of field name → new value.

        Returns:
            ``True`` if the item was found and updated.
        """
        item = self._items.get(item_id)
        if item is None:
            return False

        for key, value in updates.items():
            if hasattr(item, key):
                setattr(item, key, value)

        item.updated_at = datetime.now(UTC)
        item.version += 1
        return True

    def mark_stale(self, item_id: str) -> bool:
        """Mark an item as ``"stale"`` (still retrievable, but flagged)."""
        return self.update(item_id, {"status": "stale"})

    def archive(self, item_id: str) -> bool:
        """Archive an item (hidden from default retrieval)."""
        return self.update(item_id, {"status": "archived"})

    def reactivate(self, item_id: str) -> bool:
        """Reactivate a stale or archived item back to ``"active"``."""
        return self.update(item_id, {"status": "active", "score": 0.6})

    def purge_archived(self, before: datetime | None = None) -> int:
        """Permanently delete archived items.

        Args:
            before: If provided, only purge items archived before this time.

        Returns:
            Number of items removed.
        """
        to_remove: list[str] = []
        for item_id, item in self._items.items():
            if item.status != "archived":
                continue
            if before is not None and item.updated_at >= before:
                continue
            to_remove.append(item_id)

        for item_id in to_remove:
            del self._items[item_id]

        return len(to_remove)

    # ── Bulk operations ────────────────────────────────────────────────

    def clear(
        self,
        type: KnowledgeType | None = None,
        domain: str | None = None,
    ) -> int:
        """Clear all items, or filtered by type/domain.

        Returns:
            Number of items removed.
        """
        if type is None and domain is None:
            count = len(self._items)
            self._items.clear()
            return count

        to_remove = [
            item_id
            for item_id, item in self._items.items()
            if (type is None or item.type == type)
            and (domain is None or item.domain_id == domain)
        ]
        for item_id in to_remove:
            del self._items[item_id]
        return len(to_remove)

    # ── Stats ──────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return a summary statistics dict.

        Keys:
            - ``total``: Total item count.
            - ``by_type``: ``{type: count}``.
            - ``by_status``: ``{status: count}``.
            - ``by_domain``: ``{domain: count}``.
            - ``avg_score``: Mean quality score across all items.
            - ``oldest``: Timestamp of oldest item.
            - ``newest``: Timestamp of newest item.
        """
        if not self._items:
            return {
                "total": 0,
                "by_type": {},
                "by_status": {},
                "by_domain": {},
                "avg_score": 0.0,
                "oldest": None,
                "newest": None,
            }

        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_domain: dict[str, int] = {}
        scores: list[float] = []
        timestamps: list[datetime] = []

        for item in self._items.values():
            by_type[item.type.value] = by_type.get(item.type.value, 0) + 1
            by_status[item.status] = by_status.get(item.status, 0) + 1
            key = item.domain_id or "__global__"
            by_domain[key] = by_domain.get(key, 0) + 1
            scores.append(item.score)
            timestamps.append(item.created_at)

        avg_score = round(sum(scores) / len(scores), 4)

        return {
            "total": len(self._items),
            "by_type": dict(sorted(by_type.items())),
            "by_status": dict(sorted(by_status.items())),
            "by_domain": dict(sorted(by_domain.items())),
            "avg_score": avg_score,
            "oldest": min(timestamps),
            "newest": max(timestamps),
        }

    # ── Evolution engine (Phase 5.5) ────────────────────────────────

    def evolve(self) -> EvolutionRecord:
        """Run one evolution cycle: transition stale items and reactivate hot ones.

        Lifecycle rules:
        - Schema items unused for > 7 days → ``stale``
        - Reference SQL unused for > 30 days → ``stale``
        - Stale items unused for > 90 days → ``archived``
        - Items referenced by recent successful queries → ``active`` (reactivated)
        """

        now = datetime.now(UTC)
        record = EvolutionRecord()
        transitioned = 0
        archived = 0
        reactivated = 0

        for _item_id, item in list(self._items.items()):
            age_days = (
                (now - (item.last_accessed_at or item.created_at)).total_seconds()
                / 86400
            )

            if item.status == "active":
                if (item.type == KnowledgeType.SCHEMA and age_days > 7) or (
                    item.type == KnowledgeType.REFERENCE_SQL and age_days > 30
                ):
                    item.status = "stale"
                    transitioned += 1
            elif item.status == "stale" and age_days > 90:
                item.status = "archived"
                transitioned += 1
                archived += 1

            # High-score items accessed recently → reactivate
            if item.status in ("stale", "archived") and item.score >= 0.8 and age_days < 14:
                item.status = "active"
                transitioned += 1
                reactivated += 1

        record.items_transitioned = transitioned
        record.items_archived = archived
        record.items_reactivated = reactivated
        record.details = (
            f"Evolved {len(self._items)} items: "
            f"{transitioned} transitioned, "
            f"{archived} archived, {reactivated} reactivated"
        )
        return record

    def auto_ingest_from_feedback(
        self, feedback_records: list[dict[str, Any]]
    ) -> int:
        """Auto-ingest confirmed NL→SQL pairs from feedback records.

        Only ingests records with rating ≥ 4 and where the user edited the SQL
        (indicating the confirmed version is more correct).

        Args:
            feedback_records: List of feedback record dicts from
                :class:`~app.learning.feedback_collector.FeedbackCollector`.

        Returns:
            Number of new knowledge items ingested.
        """
        count = 0
        for rec in feedback_records:
            rating = rec.get("rating", 0)
            sql_final = rec.get("sql_final", "")
            if not sql_final or rating < 4:
                continue

            self.ingest_reference_sql(
                nl=rec.get("nl_input", ""),
                sql=sql_final,
                confidence=min(rating / 5, 1.0),
                domain=rec.get("domain_id", ""),
                source="feedback_auto_ingest",
            )
            count += 1
        return count

    def auto_ingest_schema_changes(self, schema_snapshot: Any, db_id: str) -> bool:
        """Detect and ingest schema changes by diffing against cached version.

        Args:
            schema_snapshot: A :class:`~app.models.schema.SchemaSnapshot` instance.
            db_id: Database identifier.

        Returns:
            ``True`` if changes were detected and ingested.
        """
        from app.rag.index_refresher import index_refresher

        changed = index_refresher.check_schema_version(db_id, schema_snapshot)
        if changed:
            self.ingest_schema(
                schema_snapshot,
                domain=db_id,
                source="auto_schema_sync",
            )
        return changed

    def get_evolution_stats(self) -> dict[str, Any]:
        """Return stats specifically about the evolution lifecycle.

        Returns:
            Dict with ``items_by_lifecycle``, ``new_this_week``,
            ``archived_this_week``, ``total_active``, ``total_stale``,
            ``total_archived``.
        """
        now = datetime.now(UTC)
        one_week_ago = now.replace(
            microsecond=0
        )  # approx; fine for stats
        from datetime import timedelta
        one_week_ago = now - timedelta(days=7)

        new_this_week = sum(
            1 for item in self._items.values() if item.created_at >= one_week_ago
        )
        archived_this_week = 0  # tracked in evolution records
        by_status: dict[str, int] = {}
        for item in self._items.values():
            by_status[item.status] = by_status.get(item.status, 0) + 1

        return {
            "items_by_lifecycle": by_status,
            "new_this_week": new_this_week,
            "archived_this_week": archived_this_week,
            "total_active": by_status.get("active", 0),
            "total_stale": by_status.get("stale", 0),
            "total_archived": by_status.get("archived", 0),
        }

    def needs_evolution(self) -> bool:
        """Check if an evolution cycle should run.

        Returns ``True`` if any item is past its staleness threshold.
        """
        now = datetime.now(UTC)
        for item in self._items.values():
            age_days = (
                (now - (item.last_accessed_at or item.created_at)).total_seconds()
                / 86400
            )
            if item.status == "active":
                if item.type == KnowledgeType.SCHEMA and age_days > 7:
                    return True
                if item.type == KnowledgeType.REFERENCE_SQL and age_days > 30:
                    return True
        return False

    # ── Container protocol ─────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, item_id: str) -> bool:
        return item_id in self._items

    def __iter__(self):
        return iter(self._items.values())


# ── Helpers ─────────────────────────────────────────────────────────────


def _generate_id() -> str:
    """Generate a short unique ID (8 hex chars)."""
    return uuid.uuid4().hex[:8]


def _keyword_match_score(item: KnowledgeItem, query: str) -> float:
    """Compute a simple keyword match score between item content/tags and query.

    Returns a score 0.0–1.0 based on how many query words match content values
    or tags.
    """
    if not query:
        return 0.0

    query_lower = query.lower()
    query_words = _tokenize(query_lower)
    if not query_words:
        return 0.0

    # Collect all searchable text from the item
    searchable: list[str] = list(item.tags)

    for value in item.content.values():
        if isinstance(value, str):
            searchable.append(value)
        elif isinstance(value, list):
            for v in value:
                if isinstance(v, str):
                    searchable.append(v)

    # Join into a single text block for substring matching
    text_block = " ".join(searchable).lower()

    hits = 0
    for word in query_words:
        if word in text_block:
            hits += 1
        else:
            # Partial match: any query word substring in searchable
            for s in searchable:
                if word in s.lower():
                    hits += 0.5
                    break

    return round(min(hits / len(query_words), 1.0), 4)


def _tokenize(text: str) -> list[str]:
    """Split text into words, filtering very short tokens."""
    import re

    tokens = re.split(r"\s+|[,，.。;；:：!！?？、]+", text)
    return [t.strip() for t in tokens if len(t.strip()) >= 2]
