r"""HybridSearchNode — wrap RAG hybrid search as a Harness Node.

See implementation-plan §4.8 and progress-log §3.7 for the full specification.

This node wraps the three RAG backends (SchemaMetadataRAG, MetricRAG,
DocumentStore) so they can be driven by ``WorkflowRunner`` via workflow YAML
plans.

The node supports four targeting modes via ``input.config["target"]``:

===========  =========================================================
Target        Behaviour
===========  =========================================================
``schema``    Find relevant tables/columns via ``SchemaMetadataRAG``.
``metrics``   Match business KPIs via ``MetricRAG``.
``documents`` Search platform docs via ``DocumentStore``.
``all``       Search all configured backends and merge results.
===========  =========================================================

Usage::

    node = HybridSearchNode(schema_rag=..., metric_rag=..., doc_store=...)
    output = await node.execute(NodeInput(
        query_text="monthly sales by region",
        config={"target": "schema", "top_k": 10, "db_id": "main"},
    ))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.nodes.base import BaseNode, NodeInput, NodeOutput

# ── Result model ───────────────────────────────────────────────────────


@dataclass
class HybridSearchOutput:
    """Unified output from HybridSearchNode across all three RAG backends.

    Each field is a list of backend-specific result objects (or empty if the
    backend was not queried).
    """

    query: str = ""
    target: str = "all"

    # Schema results
    schema_results: list[Any] = field(default_factory=list)

    # Metric results
    metric_results: list[Any] = field(default_factory=list)

    # Document results
    document_results: list[Any] = field(default_factory=list)

    # Summary
    total_hits: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def best_table(self) -> str | None:
        """Return the most relevant table name from schema results, if any."""
        for r in self.schema_results:
            if getattr(r, "table_name", None):
                return r.table_name
        return None

    @property
    def best_metric(self) -> str | None:
        """Return the most relevant metric name, if any."""
        for r in self.metric_results:
            if getattr(r, "name", None):
                return r.name
        return None


# ── Node ───────────────────────────────────────────────────────────────


class HybridSearchNode(BaseNode):
    """Execute hybrid search across one or more RAG backends.

    Wraps ``SchemaMetadataRAG``, ``MetricRAG``, and ``DocumentStore`` so
    they fit the Harness lifecycle (setup_input → execute → update_context).

    This node is **not** an ``AgenticNode`` — the RAG pipeline is deterministic
    (embedding + BM25 + RRF), with no LLM calls at retrieval time.

    Input config keys:
        ``target``: ``"schema"`` | ``"metrics"`` | ``"documents"`` | ``"all"``
        ``top_k``: int (default 10)
        ``db_id``: str — filter schema results to a database
        ``domain``: str — filter metric/doc results to a domain
        ``content_type``: str — filter doc results (faq, best_practice, etc.)
        ``language``: str — language hint (default ``"auto"``)

    Output context keys:
        ``hybrid_search_results`` — ``HybridSearchOutput``
    """

    name = "hybrid_search"
    description = (
        "Execute hybrid (dense + sparse) RAG search across schema metadata, "
        "business metrics, and platform documents via LanceDB + BM25 + RRF fusion"
    )

    def __init__(
        self,
        schema_rag: Any = None,
        metric_rag: Any = None,
        document_store: Any = None,
    ) -> None:
        """Create a HybridSearchNode with optional pre-configured RAG backends.

        Args:
            schema_rag: ``SchemaMetadataRAG`` instance (or ``None`` to disable).
            metric_rag: ``MetricRAG`` instance (or ``None`` to disable).
            document_store: ``DocumentStore`` instance (or ``None`` to disable).
        """
        super().__init__()
        self._schema_rag = schema_rag
        self._metric_rag = metric_rag
        self._doc_store = document_store

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def has_schema(self) -> bool:
        return self._schema_rag is not None

    @property
    def has_metrics(self) -> bool:
        return self._metric_rag is not None

    @property
    def has_documents(self) -> bool:
        return self._doc_store is not None

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def execute(self, input: NodeInput) -> NodeOutput:
        """Run hybrid search against the configured backend(s).

        Args:
            input: ``NodeInput`` whose ``query_text`` is the search query and
                   ``config`` controls the target backend(s) and filters.

        Returns:
            ``NodeOutput`` with ``HybridSearchOutput`` as ``result`` and in
            ``context["hybrid_search_results"]``.
        """
        query = input.query_text
        config = input.config

        if not query or not query.strip():
            return NodeOutput(
                result=HybridSearchOutput(errors=["Empty query"]),
                errors=["Empty query"],
                metadata={"status": "empty_query"},
            )

        target: str = config.get("target", "all")
        top_k: int = config.get("top_k", 10)
        db_id: str | None = config.get("db_id")
        domain: str | None = config.get("domain")
        content_type: str | None = config.get("content_type")
        language: str = config.get("language", "auto")

        output = HybridSearchOutput(query=query, target=target)
        errors: list[str] = []

        # ── Schema search ──────────────────────────────────────────
        if target in ("schema", "all") and self.has_schema:
            try:
                output.schema_results = await self._schema_rag.find_relevant_tables(
                    query, top_k=top_k, db_id=db_id, language=language,
                )
            except Exception as exc:
                errors.append(f"Schema search failed: {exc}")

        # ── Metric search ──────────────────────────────────────────
        if target in ("metrics", "all") and self.has_metrics:
            try:
                output.metric_results = await self._metric_rag.match_metrics(
                    query, top_k=top_k, domain=domain, language=language,
                )
            except Exception as exc:
                errors.append(f"Metric search failed: {exc}")

        # ── Document search ────────────────────────────────────────
        if target in ("documents", "all") and self.has_documents:
            try:
                output.document_results = await self._doc_store.search(
                    query, top_k=top_k, content_type=content_type,
                    domain=domain, language=language,
                )
            except Exception as exc:
                errors.append(f"Document search failed: {exc}")

        output.errors = errors
        output.total_hits = (
            len(output.schema_results)
            + len(output.metric_results)
            + len(output.document_results)
        )

        if target not in ("schema", "metrics", "documents", "all"):
            return NodeOutput(
                result=output,
                errors=[f"Unknown target '{target}'"],
                metadata={"status": "unknown_target", "target": target},
            )

        return NodeOutput(
            result=output,
            metadata={
                "status": "success",
                "target": target,
                "total_hits": output.total_hits,
                "schema_hits": len(output.schema_results),
                "metric_hits": len(output.metric_results),
                "document_hits": len(output.document_results),
                "errors": errors,
            },
            context={"hybrid_search_results": output},
        )

    async def update_context(
        self, output: NodeOutput, shared_context: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge search results into the shared workflow context.

        In addition to ``hybrid_search_results``, convenience keys are
        set for the most common downstream consumers:
          - ``relevant_tables`` — list of table names (str)
          - ``relevant_metrics`` — list of metric names (str)
          - ``relevant_docs`` — list of document titles (str)
        """
        merged = {**shared_context}
        if output.context:
            merged.update(output.context)

        result: HybridSearchOutput | None = output.result
        if result is None:
            return merged

        # Convenience: extract top table names
        if result.schema_results:
            tables: list[str] = []
            seen: set[str] = set()
            for r in result.schema_results:
                tname = getattr(r, "table_name", None)
                if tname and tname not in seen:
                    seen.add(tname)
                    tables.append(tname)
            merged.setdefault("relevant_tables", tables)

        # Convenience: extract top metric names
        if result.metric_results:
            metrics: list[str] = []
            seen_m: set[str] = set()
            for r in result.metric_results:
                mname = getattr(r, "name", None)
                if mname and mname not in seen_m:
                    seen_m.add(mname)
                    metrics.append(mname)
            merged.setdefault("relevant_metrics", metrics)

        # Convenience: extract top document titles
        if result.document_results:
            docs: list[str] = []
            seen_d: set[str] = set()
            for r in result.document_results:
                dtitle = getattr(r, "title", None)
                if dtitle and dtitle not in seen_d:
                    seen_d.add(dtitle)
                    docs.append(dtitle)
            merged.setdefault("relevant_docs", docs)

        return merged
