"""ParseNLNode — wraps the NL→SQR parsing pipeline as a Harness Node.

See SPEC §4.7.1 and implementation-plan §4.12 for the full specification.

This node is the entry point of the NL→SQL pipeline:
  NL text → NLParser (5-step pipeline) → SQR → shared context

Downstream nodes (SchemaLinkingNode, GenerateSQLNode) consume the SQR
from the shared workflow context.
"""

from __future__ import annotations

from app.core.nlp_parser import NLParser
from app.nodes.base import BaseNode, NodeInput, NodeOutput


class ParseNLNode(BaseNode):
    """Parse natural language into Structured Query Representation (SQR).

    Wraps ``NLParser`` so it fits the Harness's three-method lifecycle
    (setup_input → execute → update_context) and can be driven by
    ``WorkflowRunner``.

    This node is **not** an ``AgenticNode`` — it uses pure deterministic
    rules (regex + keyword matching), with no LLM calls.  The ``async``
    signature is kept for future compatibility when Phase 3 adds
    LLM-based entity extraction.

    Output context keys:
        ``sqr`` — the full ``SQR`` dataclass instance
    """

    name = "parse_nl"
    description = (
        "Parse natural language into Structured Query Representation (SQR) "
        "via a 5-step deterministic pipeline: language detection → time "
        "parsing → intent classification → ambiguity detection → SQR assembly"
    )

    def __init__(self, parser: NLParser | None = None) -> None:
        """Create a ParseNLNode with an optional pre-configured NLParser.

        Args:
            parser: A pre-configured ``NLParser`` instance.  If ``None``,
                    a default ``NLParser`` is created on first use.
        """
        super().__init__()
        self._parser = parser

    @property
    def parser(self) -> NLParser:
        """Lazy-initialised NLParser instance."""
        if self._parser is None:
            self._parser = NLParser()
        return self._parser

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def execute(self, input: NodeInput) -> NodeOutput:
        """Run the 5-step NL→SQR pipeline.

        Args:
            input: Standard ``NodeInput`` — ``input.query_text`` holds
                   the raw NL string.

        Returns:
            ``NodeOutput`` with the ``SQR`` as ``result`` and stored in
            ``context["sqr"]`` for downstream nodes.
        """
        nl_text = input.query_text

        if not nl_text or not nl_text.strip():
            return NodeOutput(
                result=None,
                errors=["Empty input: no natural language text to parse"],
                metadata={"status": "empty_input"},
            )

        try:
            sqr = await self.parser.parse(nl_text, context=input.context)
        except Exception as exc:
            return NodeOutput(
                result=None,
                errors=[f"NL parsing failed: {exc}"],
                metadata={"status": "parse_error"},
            )

        return NodeOutput(
            result=sqr,
            metadata={
                "status": "success",
                "language": sqr.language,
                "intent": sqr.intent.value,
                "confidence": sqr.confidence,
                "ambiguity_count": len(sqr.ambiguities),
                "table_count": len(sqr.target_tables),
                "entity_count": len(sqr.entities),
            },
            context={"sqr": sqr},
        )

    async def update_context(
        self, output: NodeOutput, shared_context: dict
    ) -> dict:
        """Merge the parsed SQR into the shared workflow context.

        The SQR is stored under the key ``"sqr"`` so downstream nodes
        (SchemaLinkingNode, GenerateSQLNode) can consume it.
        """
        merged = {**shared_context}
        if output.context:
            merged.update(output.context)
        # Also surface individual SQR fields as convenience keys
        if output.result is not None and output.metadata.get("status") == "success":
            merged.setdefault("sqr_intent", output.result.intent)
            merged.setdefault("sqr_language", output.result.language)
            merged.setdefault("sqr_confidence", output.result.confidence)
        return merged
