"""GenerateSQLNode — NL → SQL generation via LiteLLM (full implementation).

See SPEC §4.4 and implementation-plan §4.7.3 for the full specification.

Phase 2 implementation:
  - Builds LLM prompt via PromptBuilder (schema + glossary + rules + history).
  - Calls LiteLLM router for SQL generation (streaming or non-streaming).
  - Supports multi-candidate generation (temperature sampling / multi-perspective).
  - Stores generated candidates in shared context for downstream nodes.
"""

from __future__ import annotations

from app.core.prompt_builder import PromptBuilder
from app.core.sql_generator import SQLGenerator, _split_alternatives
from app.llm.router import LiteLLMRouter, RouterConfig
from app.nodes.agentic import AgenticNode
from app.nodes.base import NodeInput, NodeOutput


class GenerateSQLNode(AgenticNode):
    """Generate SQL from natural language using LiteLLM.

    Consumes the ``SQR`` from shared context (placed there by
    ``ParseNLNode``), builds a level-appropriate prompt, and generates
    one or more SQL candidates.

    Output context keys:
        ``candidates`` — ``list[SQLCandidate]``
        ``primary_sql`` — ``str | None`` (the top candidate's SQL)
        ``primary_candidate`` — ``SQLCandidate | None``
    """

    name = "generate_sql"
    description = "Generate SQL from natural language via LiteLLM multi-provider routing"

    def __init__(
        self,
        router: LiteLLMRouter | None = None,
        prompt_builder: PromptBuilder | None = None,
        generator: SQLGenerator | None = None,
    ) -> None:
        super().__init__()
        self._router = router
        self._prompt_builder = prompt_builder
        self._generator = generator

    # ------------------------------------------------------------------
    # Lazy accessors
    # ------------------------------------------------------------------

    @property
    def router(self) -> LiteLLMRouter:
        if self._router is None:
            self._router = LiteLLMRouter(RouterConfig(providers={}), mock_mode=True)
        return self._router

    @property
    def prompt_builder(self) -> PromptBuilder:
        if self._prompt_builder is None:
            self._prompt_builder = PromptBuilder()
        return self._prompt_builder

    @property
    def generator(self) -> SQLGenerator:
        if self._generator is None:
            self._generator = SQLGenerator(
                router=self.router,
                prompt_builder=self.prompt_builder,
            )
        return self._generator

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def execute(self, input: NodeInput) -> NodeOutput:
        """Generate SQL from the parsed NL query.

        Expected ``input.context`` keys:
            - ``sqr``: ``SQR`` from ParseNLNode (falls back to query_text).
            - ``schema``: Optional ``SchemaSnapshot``.
            - ``domain``: Optional ``DomainConfig``.
            - ``history``: Optional conversation history.
            - ``similar_pairs``: Optional few-shot ``QueryPair`` list.

        Expected ``input.config`` keys:
            - ``num_candidates``: int (default 1).
            - ``strategy``: ``"temperature"`` | ``"multi_perspective"``.
            - ``model``: str | None (override).
            - ``dialect``: str | None (override).
            - ``streaming``: bool (default False).
        """
        # ── Extract inputs ────────────────────────────────────────
        sqr = input.context.get("sqr")
        if sqr is None:
            # Fall back: if no SQR in context, wrap query_text
            from app.models.query import SQR

            sqr = SQR(raw_text=input.query_text)

        schema = input.context.get("schema")
        domain = input.context.get("domain")
        history = input.context.get("history")
        similar_pairs = input.context.get("similar_pairs")

        num_candidates = input.config.get("num_candidates", 1)
        strategy = input.config.get("strategy", "temperature")
        model = input.config.get("model")
        dialect = input.config.get("dialect")
        streaming = input.config.get("streaming", False)
        rag_context = input.config.get("rag_context")
        error_context = input.config.get("error_context")
        multi_table_hint = input.config.get("multi_table_hint")
        database_name = input.config.get("database_name", "")

        try:
            if streaming:
                # Streaming mode — accumulate tokens
                collected: list[str] = []
                async for chunk in self.generator.generate_stream(
                    sqr, schema=schema, domain=domain,
                    history=history, similar_pairs=similar_pairs,
                    model=model, dialect=dialect, rag_context=rag_context,
                    error_context=error_context,
                    multi_table_hint=multi_table_hint,
                    database_name=database_name,
                ):
                    collected.append(chunk)

                raw_sql = "".join(collected)
                sql_text = _extract_sql(raw_sql)

                from app.models.query import SQLCandidate

                # ── Split multi-table alternatives in streaming mode ──
                import uuid as _uuid

                alternatives = _split_alternatives(raw_sql)
                if len(alternatives) > 1:
                    candidates = []
                    for j, alt_sql in enumerate(alternatives):
                        mode = "primary" if j == 0 else f"alt_{j}"
                        candidates.append(
                            SQLCandidate(
                                id=f"cand_{_uuid.uuid4().hex[:8]}",
                                sql_text=alt_sql,
                                confidence=max(0.85 - j * 0.12, 0.3),
                                generation_mode=mode,
                                dialect=dialect or "ansi",
                                model=model or self.router.config.default_model,
                                database_name=database_name,
                            )
                        )
                else:
                    candidate = SQLCandidate(
                        id=f"cand_{_uuid.uuid4().hex[:8]}",
                        sql_text=sql_text,
                        confidence=0.85,
                        generation_mode="stream",
                        dialect=dialect or "ansi",
                        model=model or self.router.config.default_model,
                        database_name=database_name,
                    )
                    candidates = [candidate]
            else:
                candidates = await self.generator.generate(
                    sqr, schema=schema, domain=domain,
                    history=history, similar_pairs=similar_pairs,
                    num_candidates=num_candidates, strategy=strategy,
                    model=model, dialect=dialect, rag_context=rag_context,
                    error_context=error_context,
                    multi_table_hint=multi_table_hint,
                    database_name=database_name,
                )
        except Exception as exc:
            return NodeOutput(
                result={"candidates": [], "primary_sql": None},
                errors=[f"SQL generation failed: {exc}"],
                metadata={"status": "generation_error", "candidates_count": 0},
            )

        primary = candidates[0] if candidates else None

        return NodeOutput(
            result={
                "candidates": candidates,
                "primary_sql": primary.sql_text if primary else None,
            },
            metadata={
                "status": "success",
                "candidates_count": len(candidates),
                "primary_sql": primary.sql_text[:200] if primary and primary.sql_text else None,
                "confidence": primary.confidence if primary else 0.0,
            },
            context={
                "candidates": candidates,
                "primary_sql": primary.sql_text if primary else None,
                "primary_candidate": primary,
            },
        )


# ── Module-level helper (re-exported for convenience) ──────────────────


def _extract_sql(text: str) -> str:
    """Strip markdown fences and preamble from LLM output."""
    import re

    if not text:
        return ""
    fence = re.compile(
        r"```(?:sql|SQL|mysql|postgresql|postgres|sqlite)?\s*\n(.*?)```",
        re.DOTALL | re.IGNORECASE,
    )
    matches = fence.findall(text)
    if matches:
        return matches[0].strip()
    cleaned = text.strip()
    for prefix in [
        "Here is the SQL query:",
        "Here's the SQL:",
        "SQL:",
        "The query is:",
        "Generated SQL:",
    ]:
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix):].strip()
    return cleaned
