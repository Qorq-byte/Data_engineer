"""SQL Generation Agent — wraps PromptBuilder + SQLGenerator via LiteLLM.

See SPEC §4.11.2 (agent role: SQL Generation) and implementation plan §4.7.3.

Supports two strategies:
  - ``temperature`` — single LLM call with n > 1
  - ``multi_perspective`` — N independent calls with distinct focus angles
Also supports ``retry_with_correction`` for the back-pass correction flow.
"""

from __future__ import annotations

import time
from typing import Any

from app.agents.base import AgentResult, BaseAgent
from app.models.query import SQLCandidate


class SQLGenerationAgent(BaseAgent):
    """Agent that generates SQL candidates from an SQR and schema.

    Wraps :class:`app.core.sql_generator.SQLGenerator`.

    Usage::

        agent = SQLGenerationAgent(generator=gen)
        result = await agent.execute({"sqr": sqr, "schema": schema, "domain": domain})
        candidates: list[SQLCandidate] = result.data
    """

    name = "sql_generator"
    description = (
        "Generate SQL candidates from a structured query representation (SQR) "
        "using LiteLLM with temperature-sampling or multi-perspective strategies."
    )

    def __init__(
        self,
        generator: Any = None,
        prompt_builder: Any = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(config)
        self._generator = generator
        self._prompt_builder = prompt_builder

    async def execute(self, input: Any, context: dict[str, Any]) -> AgentResult:
        """Generate SQL candidates.

        Args:
            input: dict with keys ``sqr`` (SQR), ``schema`` (SchemaSnapshot),
                   ``domain`` (DomainConfig).  May also contain:
                     - ``strategy`` — "temperature" (default) or "multi_perspective"
                     - ``num_candidates`` — how many to produce (default 3)
            context: Shared dict (session_id, db_id, domain_id, etc.).

        Returns:
            AgentResult.data = list[SQLCandidate].
        """
        t0 = time.perf_counter()
        try:
            sqr, schema, domain = self._resolve_input(input, context)
            strategy = (
                input.get("strategy", "temperature")
                if isinstance(input, dict)
                else "temperature"
            )
            num = int(input.get("num_candidates", 3)) if isinstance(input, dict) else 3
            model = (isinstance(input, dict) and input.get("model")) or None

            candidates: list[SQLCandidate] = []
            if self._generator is not None:
                if model:
                    # Some generators accept model override
                    candidates = await self._generator.generate(
                        sqr, schema, domain,
                        num_candidates=num,
                        strategy=strategy,
                        model=model,
                    )
                else:
                    candidates = await self._generator.generate(
                        sqr, schema, domain,
                        num_candidates=num,
                        strategy=strategy,
                    )

            elapsed = (time.perf_counter() - t0) * 1000
            return self._ok(
                candidates,
                latency_ms=round(elapsed, 2),
                candidate_count=len(candidates),
                strategy=strategy,
                model=model or "default",
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return self._error(str(exc), latency_ms=round(elapsed, 2))

    async def retry_with_correction(
        self,
        previous_sql: str,
        error: str,
        context: dict[str, Any],
        sqr: Any = None,
        schema: Any = None,
        domain: Any = None,
    ) -> AgentResult:
        """Generate a corrected SQL after a validation/execution failure.

        This is the core of the *back-pass correction* collaboration mode.
        """
        t0 = time.perf_counter()
        try:
            candidates: list[SQLCandidate] = []
            if self._generator is not None:
                candidates = await self._generator.generate(
                    sqr,
                    schema,
                    domain,
                    num_candidates=1,
                    strategy="temperature",
                    correction_context={
                        "previous_sql": previous_sql,
                        "error": error,
                    },
                )
            elapsed = (time.perf_counter() - t0) * 1000
            return self._ok(
                candidates,
                latency_ms=round(elapsed, 2),
                candidate_count=len(candidates),
                retry=True,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return self._error(str(exc), latency_ms=round(elapsed, 2))

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_input(input: Any, context: dict[str, Any]) -> tuple:
        """Extract (sqr, schema, domain) from input dict or context."""
        sqr = schema = domain = None
        if isinstance(input, dict):
            sqr = input.get("sqr") or context.get("sqr")
            schema = input.get("schema") or context.get("schema")
            domain = input.get("domain") or context.get("domain")
        return sqr, schema, domain
