"""NL Understanding Agent — wraps the 5-step NL→SQR parsing pipeline.

See SPEC §4.11.2 (agent role: NL Understanding) and implementation plan §4.7.1.

This agent is a thin wrapper around ``app.core.nlp_parser.NLParser`` that
adds tracing and the standard ``AgentResult`` envelope.  The underlying parser
is fully deterministic (no LLM), so this agent is fast and free.
"""

from __future__ import annotations

import time
from typing import Any

from app.agents.base import AgentResult, BaseAgent
from app.models.query import SQR


class NLUnderstandingAgent(BaseAgent):
    """Agent that parses natural language into a structured SQR.

    Wraps :class:`app.core.nlp_parser.NLParser`.  The 5-step pipeline is:
      1. Language detection (zh / en / mixed)
      2. Time expression extraction
      3. Intent classification (7 types)
      4. Ambiguity detection (4 categories)
      5. SQR assembly

    Usage::

        from app.core.nlp_parser import NLParser
        agent = NLUnderstandingAgent(parser=NLParser())
        result = await agent.execute("上个月订单总数", context={})
        sqr: SQR = result.data
    """

    name = "nl_understander"
    description = (
        "Parse natural language into a structured query representation (SQR): "
        "language detection, time extraction, intent classification, ambiguity detection."
    )

    def __init__(self, parser: Any = None, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        if parser is None:
            from app.core.nlp_parser import NLParser as _NLParser

            parser = _NLParser()
        self._parser = parser

    async def execute(self, input: Any, context: dict[str, Any]) -> AgentResult:
        """Parse *input* (NL text) into an SQR.

        Args:
            input: Natural language query string (zh or en).
            context: Optional dict with keys like ``domain_id``, ``session_id``.

        Returns:
            AgentResult with ``data`` = :class:`SQR`.
        """
        t0 = time.perf_counter()
        try:
            nl_text = self._resolve_input(input, context)
            sqr: SQR = await self._parser.parse(nl_text, context)
            elapsed = (time.perf_counter() - t0) * 1000
            return self._ok(
                sqr,
                latency_ms=round(elapsed, 2),
                language=sqr.language,
                intent=sqr.intent.value if sqr.intent else "unknown",
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return self._error(str(exc), latency_ms=round(elapsed, 2))

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_input(input: Any, context: dict[str, Any]) -> str:
        """Accept str or dict with 'nl_text' / 'query_text' / 'text' key."""
        if isinstance(input, str):
            return input
        if isinstance(input, dict):
            return input.get("nl_text") or input.get("query_text") or input.get("text") or ""
        return str(input)
