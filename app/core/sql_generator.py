"""SQL Generator — multi-candidate SQL generation via LiteLLM.

See SPEC §4.7.3 and implementation-plan §4.7.3 for the full design.

Two strategies:
  - **temperature sampling** — single call with n > 1 and variable temps.
  - **multi-perspective** — N independent calls each with a distinct focus
    (performance, readability, completeness).
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, Literal

from app.core.prompt_builder import PromptBuilder
from app.llm.completion import IncompleteCompletionError, check_completion, response_field
from app.llm.protocols import CompletionRouter
from app.llm.streaming import StreamEvent, events_for
from app.models.domain import DomainConfig
from app.models.query import SQR, QueryPair, SQLCandidate
from app.models.schema import SchemaSnapshot

# ── Perspective prompts for multi-perspective strategy ──────────────────

_PERSPECTIVES: list[str] = [
    "Prioritize query performance — ensure index usage and avoid full table scans.",
    "Prioritize code readability — use clear CTEs, aliases, and formatting.",
    "Prioritize functional completeness — handle NULLs, edge cases, and all filters.",
]


class SQLGenerator:
    """Generate SQL candidates from an SQR using an LLM router.

    Usage::

        gen = SQLGenerator(router, prompt_builder)
        candidates = await gen.generate(sqr, schema, domain)
        # Or streaming:
        async for token in gen.generate_stream(sqr, schema, domain):
            print(token, end="")
    """

    def __init__(
        self,
        router: CompletionRouter,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self.router = router
        self.prompt_builder = prompt_builder or PromptBuilder()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate(
        self,
        sqr: SQR,
        schema: SchemaSnapshot | None = None,
        domain: DomainConfig | None = None,
        history: list[Any] | None = None,
        similar_pairs: list[QueryPair] | None = None,
        num_candidates: int = 1,
        strategy: str = "temperature",
        model: str | None = None,
        dialect: str | None = None,
        rag_context: str | None = None,
        error_context: str | None = None,
        multi_table_hint: dict[str, Any] | None = None,
        database_name: str = "",
        warnings: list[str] | None = None,
    ) -> list[SQLCandidate]:
        """Generate one or more SQL candidates.

        Args:
            sqr: The parsed Structured Query Representation.
            schema: Optional DB schema snapshot for prompt injection.
            domain: Optional domain config (glossary + rules).
            history: Optional conversation history.
            similar_pairs: Optional few-shot NL→SQL examples.
            num_candidates: How many candidates to generate (1–5).
            strategy: ``"temperature"`` (single call, n=num) or
                      ``"multi_perspective"`` (N independent calls).
            model: Model override (default: router's default).
            dialect: SQL dialect override.
            rag_context: Optional RAG knowledge text for no-schema mode.
            error_context: Optional error info for L4 self-heal retries.
            multi_table_hint: Optional schema-linking hint listing tables
                              that independently contain matching columns.
            warnings: Optional request-local list receiving discarded completion diagnostics.

        Returns:
            A list of ``SQLCandidate`` instances, ordered by confidence
            (primary first, then alternates).
        """
        if strategy == "multi_perspective" and num_candidates > 1:
            return await self._multi_perspective(
                sqr, schema, domain, history, similar_pairs,
                num_candidates, model, dialect, rag_context,
                error_context, multi_table_hint, database_name, warnings,
            )
        return await self._temperature_sampling(
            sqr, schema, domain, history, similar_pairs,
            num_candidates, model, dialect, rag_context,
            error_context, multi_table_hint, database_name, warnings,
        )

    async def generate_stream(
        self,
        sqr: SQR,
        schema: SchemaSnapshot | None = None,
        domain: DomainConfig | None = None,
        history: list[Any] | None = None,
        similar_pairs: list[QueryPair] | None = None,
        model: str | None = None,
        dialect: str | None = None,
        rag_context: str | None = None,
        error_context: str | None = None,
        multi_table_hint: dict[str, Any] | None = None,
        database_name: str = "",
    ) -> AsyncIterator[str]:
        """Stream provisional text while retaining existing caller compatibility."""
        stream = self.generate_stream_events(
            sqr, schema=schema, domain=domain, history=history, similar_pairs=similar_pairs,
            model=model, dialect=dialect, rag_context=rag_context, error_context=error_context,
            multi_table_hint=multi_table_hint, database_name=database_name,
        )
        try:
            async for event in stream:
                if event.kind == "delta":
                    yield event.text
        finally:
            await stream.aclose()

    async def generate_stream_events(
        self,
        sqr: SQR,
        schema: SchemaSnapshot | None = None,
        domain: DomainConfig | None = None,
        history: list[Any] | None = None,
        similar_pairs: list[QueryPair] | None = None,
        model: str | None = None,
        dialect: str | None = None,
        rag_context: str | None = None,
        error_context: str | None = None,
        multi_table_hint: dict[str, Any] | None = None,
        database_name: str = "",
    ) -> AsyncGenerator[StreamEvent, None]:
        """Yield deltas and terminal evidence after complete consumption."""
        prompt = self.prompt_builder.build(
            sqr, schema=schema, domain=domain,
            history=history, similar_pairs=similar_pairs,
            dialect=dialect, rag_context=rag_context,
            error_context=error_context,
            multi_table_hint=multi_table_hint,
            database_name=database_name,
        )
        stream = events_for(
            self.router, "complete_stream", "complete_stream_events",
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.3,
        )
        try:
            async for chunk in stream:
                yield chunk
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()

    # ------------------------------------------------------------------
    # Strategies
    # ------------------------------------------------------------------

    async def _temperature_sampling(
        self,
        sqr: SQR,
        schema: SchemaSnapshot | None,
        domain: DomainConfig | None,
        history: list[Any] | None,
        similar_pairs: list[QueryPair] | None,
        n: int,
        model: str | None,
        dialect: str | None,
        rag_context: str | None = None,
        error_context: str | None = None,
        multi_table_hint: dict[str, Any] | None = None,
        database_name: str = "",
        warnings: list[str] | None = None,
    ) -> list[SQLCandidate]:
        """Strategy A: single call with variable temperature or n > 1.

        When *multi_table_hint* has alternatives, the LLM is instructed to
        return multiple SQLs separated by ``---ALTERNATIVE---``.  Each
        alternative becomes a separate candidate.
        """
        prompt = self.prompt_builder.build(
            sqr, schema=schema, domain=domain,
            history=history, similar_pairs=similar_pairs,
            dialect=dialect, rag_context=rag_context,
            error_context=error_context,
            multi_table_hint=multi_table_hint,
            database_name=database_name,
        )

        effective_n = max(1, min(n, 5))
        t0 = time.perf_counter()

        # Make n independent calls with n=1 each for provider compatibility
        # (many providers like DeepSeek don't support n>1)
        tasks = []
        for _i in range(effective_n):
            tasks.append(
                self.router.complete(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    temperature=0.3,
                    n=1,
                )
            )
        responses_raw = await asyncio.gather(*tasks, return_exceptions=True)
        latency = (time.perf_counter() - t0) * 1000

        # Parse choices into candidates
        candidates: list[SQLCandidate] = []
        idx = 0
        rejected: list[IncompleteCompletionError] = []
        for resp in responses_raw:
            if isinstance(resp, Exception):
                if isinstance(resp, IncompleteCompletionError):
                    rejected.append(resp)
                continue

            if isinstance(resp, asyncio.CancelledError):
                raise resp
            try:
                content = _completion_text(resp)
            except IncompleteCompletionError as exc:
                rejected.append(exc)
                continue
            if content is None:
                continue
            finish_reason, actual_model = _response_metadata(
                resp, model or self.router.config.default_model,
            )

            # ── Parse multi-SQL response ──────────────────────────
            # When multi_table_hint is active, the LLM may return several
            # SQLs separated by ---ALTERNATIVE---.  Split them into
            # independent candidates; the fixed workflow validates/executes only the primary.
            alternatives = _split_alternatives(content)
            if len(alternatives) > 1:
                for j, alt_sql in enumerate(alternatives):
                    mode = "primary" if (idx == 0 and j == 0) else f"alt_{idx}_{j}"
                    candidates.append(
                        SQLCandidate(
                            id=f"cand_{uuid.uuid4().hex[:8]}",
                            sql_text=alt_sql,
                            confidence=max(0.85 - j * 0.12, 0.3),
                            generation_mode=mode,
                            dialect=dialect or "ansi",
                            model=str(actual_model),
                            generation_source="model_response",
                            finish_reason=finish_reason,
                            latency_ms=latency / effective_n,
                            database_name=database_name,
                        )
                    )
            else:
                sql_text = _extract_sql(content)
                mode = "primary" if idx == 0 else f"alt_{idx}"

                candidates.append(
                    SQLCandidate(
                        id=f"cand_{uuid.uuid4().hex[:8]}",
                        sql_text=sql_text,
                        confidence=max(0.85 - idx * 0.1, 0.3),
                        generation_mode=mode,
                        dialect=dialect or "ansi",
                        model=str(actual_model),
                        generation_source="model_response",
                        finish_reason=finish_reason,
                        latency_ms=latency / effective_n,
                        database_name=database_name,
                    )
                )
            idx += 1

        if not candidates or not any(c.sql_text.strip() for c in candidates):
            if rejected:
                raise rejected[0]
            raise RuntimeError("All SQL generation attempts failed or returned empty output")
        if warnings is not None:
            warnings.extend(f"Discarded incomplete SQL response: {error}" for error in rejected)
        return [c for c in candidates if c.sql_text.strip()]

    async def _multi_perspective(
        self,
        sqr: SQR,
        schema: SchemaSnapshot | None,
        domain: DomainConfig | None,
        history: list[Any] | None,
        similar_pairs: list[QueryPair] | None,
        n: int,
        model: str | None,
        dialect: str | None,
        rag_context: str | None = None,
        error_context: str | None = None,
        multi_table_hint: dict[str, Any] | None = None,
        database_name: str = "",
        warnings: list[str] | None = None,
    ) -> list[SQLCandidate]:
        """Strategy B: N independent calls with distinct perspectives."""
        base_prompt = self.prompt_builder.build(
            sqr, schema=schema, domain=domain,
            history=history, similar_pairs=similar_pairs,
            dialect=dialect, rag_context=rag_context,
            error_context=error_context,
            multi_table_hint=multi_table_hint,
            database_name=database_name,
        )

        perspectives = _PERSPECTIVES[: min(n, len(_PERSPECTIVES))]
        tasks = []
        for p_text in perspectives:
            full_prompt = f"{base_prompt}\n\nAdditional instruction: {p_text}"
            tasks.append(
                self.router.complete(
                    messages=[{"role": "user", "content": full_prompt}],
                    model=model,
                    temperature=0.3,
                )
            )

        t0 = time.perf_counter()
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        total_latency = (time.perf_counter() - t0) * 1000

        candidates: list[SQLCandidate] = []
        rejected: list[IncompleteCompletionError] = []
        for i, resp in enumerate(responses):
            if isinstance(resp, Exception):
                if isinstance(resp, IncompleteCompletionError):
                    rejected.append(resp)
                continue

            if isinstance(resp, asyncio.CancelledError):
                raise resp
            try:
                content = _completion_text(resp)
            except IncompleteCompletionError as exc:
                rejected.append(exc)
                continue
            if content is None:
                continue

            finish_reason, actual_model = _response_metadata(
                resp, model or self.router.config.default_model,
            )

            # ── Parse multi-SQL response ──────────────────────────
            alternatives = _split_alternatives(content)
            if len(alternatives) > 1:
                for j, alt_sql in enumerate(alternatives):
                    mode = "primary" if not candidates and j == 0 else f"alt_{i}_{j}"
                    candidates.append(
                        SQLCandidate(
                            id=f"cand_{uuid.uuid4().hex[:8]}",
                            sql_text=alt_sql,
                            confidence=max(0.85 - j * 0.12, 0.3),
                            generation_mode=mode,
                            reasoning=perspectives[i] if i < len(perspectives) else "",
                            dialect=dialect or "ansi",
                            model=actual_model,
                            generation_source="model_response",
                            finish_reason=finish_reason,
                            latency_ms=total_latency / n if n > 0 else total_latency,
                            database_name=database_name,
                        )
                    )
            else:
                mode = "primary" if not candidates else f"alt_{i}"
                candidates.append(
                    SQLCandidate(
                        id=f"cand_{uuid.uuid4().hex[:8]}",
                        sql_text=_extract_sql(content),
                        confidence=max(0.85 - i * 0.1, 0.3),
                        generation_mode=mode,
                        reasoning=perspectives[i] if i < len(perspectives) else "",
                        dialect=dialect or "ansi",
                        model=actual_model,
                        generation_source="model_response",
                        finish_reason=finish_reason,
                        latency_ms=total_latency / n if n > 0 else total_latency,
                        database_name=database_name,
                    )
                )

        if not candidates or not any(c.sql_text.strip() for c in candidates):
            if rejected:
                raise rejected[0]
            raise RuntimeError("All SQL generation attempts failed or returned empty output")
        if warnings is not None:
            warnings.extend(f"Discarded incomplete SQL response: {error}" for error in rejected)
        return [c for c in candidates if c.sql_text.strip()]


# ── Helpers ────────────────────────────────────────────────────────────


def _response_metadata(
    response: object, requested_model: str,
) -> tuple[Literal["stop"] | None, str]:
    """Read only this validated response; a missing marker remains unknown."""
    choices = response_field(response, "choices")
    reason: Literal["stop"] | None = (
        "stop" if response_field(choices[0], "finish_reason") == "stop" else None
    )
    actual_model = response_field(response, "model")
    model = actual_model if isinstance(actual_model, str) and actual_model else requested_model
    return reason, model


def _completion_text(response: object) -> str | None:
    """Reject malformed/non-text choices independently, preserving valid siblings."""
    choices = (
        response.get("choices")
        if isinstance(response, dict) else getattr(response, "choices", None)
    )
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    check_completion(first)
    message = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
    content = (
        message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    )
    return content if isinstance(content, str) and content.strip() else None


def _split_alternatives(text: str) -> list[str]:
    """Split a multi-SQL response on ``---ALTERNATIVE---`` markers.

    Each fragment is cleaned via ``_extract_sql()``.  Returns at least
    one element (the original text if no marker is found).  Empty
    fragments are discarded.
    """
    if not text or "---ALTERNATIVE---" not in text:
        return []

    parts = text.split("---ALTERNATIVE---")
    result: list[str] = []
    for part in parts:
        sql = _extract_sql(part.strip())
        if sql and not sql.startswith("-- Failed to generate"):
            result.append(sql)
    return result if len(result) > 1 else []


def _extract_sql(text: str) -> str:
    """Extract clean SQL from an LLM response.

    Strips markdown code fences (```sql ... ```), leading/trailing
    whitespace, and common preamble/suffix text.
    """
    if not text:
        return ""

    # Try to extract from markdown code block first
    fence_pattern = re.compile(
        r"```(?:sql|SQL|mysql|postgresql|postgres|sqlite)?\s*\n(.*?)```",
        re.DOTALL | re.IGNORECASE,
    )
    match = fence_pattern.search(text)
    if match:
        return match.group(1).strip()

    # No fence — use the whole text after stripping common prefixes
    cleaned = text.strip()
    # Remove common LLM preamble phrases
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


def _fallback_candidate(sqr: SQR, dialect: str | None = None) -> SQLCandidate:
    """Return a minimal candidate when generation produced nothing."""
    return SQLCandidate(
        id=f"cand_{uuid.uuid4().hex[:8]}",
        sql_text=f"-- Failed to generate SQL for: {sqr.raw_text[:100]}",
        confidence=0.0,
        generation_mode="fallback",
        dialect=dialect or "ansi",
    )
