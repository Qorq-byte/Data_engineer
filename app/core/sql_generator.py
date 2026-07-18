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
from collections.abc import AsyncIterator
from typing import Any

from app.core.prompt_builder import PromptBuilder
from app.llm.router import LiteLLMRouter
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
        router: LiteLLMRouter,
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

        Returns:
            A list of ``SQLCandidate`` instances, ordered by confidence
            (primary first, then alternates).
        """
        if strategy == "multi_perspective" and num_candidates > 1:
            return await self._multi_perspective(
                sqr, schema, domain, history, similar_pairs,
                num_candidates, model, dialect, rag_context,
                error_context, multi_table_hint, database_name,
            )
        return await self._temperature_sampling(
            sqr, schema, domain, history, similar_pairs,
            num_candidates, model, dialect, rag_context,
            error_context, multi_table_hint, database_name,
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
        """Stream SQL generation token-by-token.

        Yields text chunks as the LLM generates them.  The caller is
        responsible for reassembling the full SQL.
        """
        prompt = self.prompt_builder.build(
            sqr, schema=schema, domain=domain,
            history=history, similar_pairs=similar_pairs,
            dialect=dialect, rag_context=rag_context,
            error_context=error_context,
            multi_table_hint=multi_table_hint,
            database_name=database_name,
        )
        async for chunk in self.router.complete_stream(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.3,
        ):
            yield chunk

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
        actual_model = model or self.router.config.default_model
        idx = 0
        for resp in responses_raw:
            if isinstance(resp, Exception):
                continue

            content = ""
            if isinstance(resp, dict) and "choices" in resp:
                choices = resp["choices"]
                actual_model = resp.get("model", actual_model)
                if choices:
                    choice = choices[0]
                    if isinstance(choice, dict):
                        msg = choice.get("message", {})
                        content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
            elif hasattr(resp, "choices"):
                choices = resp.choices
                if choices:
                    content = getattr(choices[0].message, "content", "")

            # ── Parse multi-SQL response ──────────────────────────
            # When multi_table_hint is active, the LLM may return several
            # SQLs separated by ---ALTERNATIVE---.  Split them into
            # independent candidates so each gets validated & executed.
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
                        latency_ms=latency / effective_n,
                        database_name=database_name,
                    )
                )
            idx += 1

        return candidates if candidates else [_fallback_candidate(sqr, dialect)]

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
        for i, resp in enumerate(responses):
            if isinstance(resp, Exception):
                continue

            content = ""
            if isinstance(resp, dict) and "choices" in resp:
                choices = resp["choices"]
                if choices:
                    choice = choices[0]
                    if isinstance(choice, dict):
                        msg = choice.get("message", {})
                        content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
            elif hasattr(resp, "choices"):
                choices = resp.choices
                if choices:
                    content = getattr(choices[0].message, "content", "")

            # ── Parse multi-SQL response ──────────────────────────
            alternatives = _split_alternatives(content)
            if len(alternatives) > 1:
                for j, alt_sql in enumerate(alternatives):
                    mode = "primary" if (i == 0 and j == 0) else f"alt_{i}_{j}"
                    candidates.append(
                        SQLCandidate(
                            id=f"cand_{uuid.uuid4().hex[:8]}",
                            sql_text=alt_sql,
                            confidence=max(0.85 - j * 0.12, 0.3),
                            generation_mode=mode,
                            reasoning=perspectives[i] if i < len(perspectives) else "",
                            dialect=dialect or "ansi",
                            model=model or self.router.config.default_model,
                            latency_ms=total_latency / n if n > 0 else total_latency,
                            database_name=database_name,
                        )
                    )
            else:
                mode = "primary" if i == 0 else f"alt_{i}"
                candidates.append(
                    SQLCandidate(
                        id=f"cand_{uuid.uuid4().hex[:8]}",
                        sql_text=_extract_sql(content),
                        confidence=max(0.85 - i * 0.1, 0.3),
                        generation_mode=mode,
                        reasoning=perspectives[i] if i < len(perspectives) else "",
                        dialect=dialect or "ansi",
                        model=model or self.router.config.default_model,
                        latency_ms=total_latency / n if n > 0 else total_latency,
                        database_name=database_name,
                    )
                )

        return candidates if candidates else [_fallback_candidate(sqr, dialect)]


# ── Helpers ────────────────────────────────────────────────────────────


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
    matches = fence_pattern.findall(text)
    if matches:
        return matches[0].strip()

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
