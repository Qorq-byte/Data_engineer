"""Schema Retrieval Agent — wraps the 5-step hierarchical schema retrieval.

See SPEC §4.11.2 (agent role: Schema Retrieval) and implementation plan §4.8.

This agent bundles SchemaRetriever, GlossaryManager, and RuleEngine into a
single retrieval step that, given an SQR, returns candidates tables, matched
terms, and applicable business rules.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from app.agents.base import AgentResult, BaseAgent
from app.models.query import SQR


class SchemaRetrievalAgent(BaseAgent):
    """Agent that retrieves relevant schema objects for a given SQR.

    Combines:
      - :class:`SchemaRetriever` — 5-step (Analyse → RAG → Term → FK → Inject)
      - :class:`GlossaryManager` — business term → SQL expression mapping
      - :class:`RuleEngine` — business rule matching and enforcement

    Usage::

        agent = SchemaRetrievalAgent(retriever=retriever)
        result = await agent.execute(sqr, context={"db_id": "main", "domain_id": "ecommerce"})
        output = result.data  # {"filtered_schema": SchemaSnapshot, "terms": [...], "rules": [...]}
    """

    name = "schema_retriever"
    description = (
        "Retrieve relevant tables, columns, terms, and rules via RAG hybrid search, "
        "glossary lookup, and FK expansion."
    )

    def __init__(
        self,
        retriever: Any = None,
        glossary: Any = None,
        rules: Any = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(config)
        self._retriever = retriever
        self._glossary = glossary
        self._rules = rules

    async def execute(self, input: Any, context: dict[str, Any]) -> AgentResult:
        """Retrieve schema objects relevant to *input* (an SQR or NL text).

        Args:
            input: SQR dataclass or raw NL text string.
            context: dict with ``db_id``, ``domain_id``, ``schema`` (SchemaSnapshot).

        Returns:
            AgentResult.data = dict with keys:
              - ``filtered_schema``: SchemaSnapshot of candidate tables
              - ``tables``: list[str] of matched table names
              - ``terms``: list[GlossaryTerm] matched business terms
              - ``rules``: list[BusinessRule] matched rules
        """
        t0 = time.perf_counter()
        sqr = self._coerce_sqr(input)
        try:
            db_schema = context.get("schema")
            context.get("db_id", "")
            domain_id = context.get("domain_id", "")

            # ── Schema retrieval ──────────────────────────────────
            filtered_schema = None
            tables: list[str] = []
            if self._retriever is not None and db_schema is not None:
                result = await self._retriever.retrieve(
                    query=sqr.raw_text if sqr else "",
                    schema=db_schema,
                    sqr=sqr,
                )
                filtered_schema = result.filtered_schema
                tables = result.tables

            # ── Glossary matching ─────────────────────────────────
            terms: list = []
            if self._glossary is not None and sqr is not None:
                with contextlib.suppress(Exception):
                    terms = await self._glossary.match(
                        sqr.raw_text, domain_id=domain_id
                    )

            # ── Rule matching ─────────────────────────────────────
            matched_rules: list = []
            if self._rules is not None and sqr is not None:
                with contextlib.suppress(Exception):
                    matched_rules = await self._rules.match(
                        sqr.raw_text, domain_id=domain_id
                    )

            elapsed = (time.perf_counter() - t0) * 1000
            return self._ok(
                {
                    "filtered_schema": filtered_schema,
                    "tables": tables,
                    "terms": terms,
                    "rules": matched_rules,
                },
                latency_ms=round(elapsed, 2),
                table_count=len(tables),
                term_count=len(terms),
                rule_count=len(matched_rules),
            )

        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return self._error(str(exc), latency_ms=round(elapsed, 2))

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _coerce_sqr(input: Any) -> SQR | None:
        """Accept SQR, dict with 'sqr' or 'nl_text', or raw str."""
        if isinstance(input, SQR):
            return input
        if isinstance(input, dict):
            sqr = input.get("sqr")
            if sqr is not None:
                return sqr
            # Create a minimal SQR from raw text
            nl = input.get("nl_text") or input.get("query_text") or input.get("text") or ""
            if nl:
                return SQR(raw_text=nl, language="zh")
            return None
        if isinstance(input, str):
            return SQR(raw_text=input, language="zh")
        return None
