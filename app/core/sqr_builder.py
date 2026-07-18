"""SQR builder — assembles Structured Query Representation from parsed NL components.

See SPEC §4.7.1 (e) and implementation-plan §4.7.1 for design rationale.

Heuristic extraction only — no inference, no schema context needed for MVP.
Schema-aware column/table resolution is deferred to SchemaLinkingNode (Task 2.11).
"""

from __future__ import annotations

import re

from app.models.query import (
    SQR,
    Ambiguity,
    Entity,
    IntentType,
    Language,
    OrderSpec,
    TimeRange,
)

# ── Limit patterns ─────────────────────────────────────────────────────
# (pattern, language)
_LIMIT_PATTERNS: list[tuple[str, str]] = [
    # Chinese
    (r"前\s*(\d+)\s*(?:名|个|条|位|项|行|笔|大|多|的)", "zh"),
    (r"(?:取|只要)\s*(?:前\s*)?(\d+)\s*(?:名|个|条|位|项|行|笔)", "zh"),
    (r"限制\s*(\d+)", "zh"),
    (r"限量\s*(\d+)", "zh"),
    # English
    (r"\b(?:top|first)\s+(\d+)", "en"),
    (r"\b(\d+)\s+(?:results?|records?|rows?|items?)\b", "en"),
    # Universal (both zh + en)
    (r"\b[Ll][Ii][Mm][Ii][Tt]\s+(\d+)", "both"),
    (r"\b[Tt][Oo][Pp]\s*(\d+)", "both"),
]


def _lang_ok(pat_lang: str, text_lang: str) -> bool:
    """Check whether *pat_lang* applies to *text_lang*."""
    if pat_lang == "both":
        return True
    if text_lang == "mixed":
        return True
    return pat_lang == text_lang


# ── Order patterns ─────────────────────────────────────────────────────
# (pattern, language, default_direction — None means unspecified)
_ORDER_PATTERNS: list[tuple[str, str, str | None]] = [
    # Chinese — explicit direction
    (
        r"(?:按|按照|根据|以)\s*(\S+?)\s*(?:升序|递增|从小到大|从低到高)",
        "zh",
        "ASC",
    ),
    (
        r"(?:按|按照|根据|以)\s*(\S+?)\s*(?:降序|递减|从大到小|从高到低)",
        "zh",
        "DESC",
    ),
    # Chinese — directionless: column char-by-char guarded to avoid
    # capturing direction/sort keywords (which have no spaces in Chinese).
    (
        r"(?:按|按照|根据|以)\s*((?:(?!升序|降序|递增|递减|从[大小高低]|排序|排列)\S)+)\s*(?:排序|排列)",
        "zh",
        None,
    ),
    # Chinese — superlative (implicit direction)
    (r"(\S+?)(?:最高|最大|最多|最快|最好|最长)", "zh", "DESC"),
    (r"(\S+?)(?:最低|最小|最少|最慢|最差|最短)", "zh", "ASC"),
    # English — explicit direction
    (
        r"\b(?:order(?:ed)?\s+by|sort(?:ed)?\s+by)\s+(\S+)\s+(?:asc|ascending)",
        "en",
        "ASC",
    ),
    (
        r"\b(?:order(?:ed)?\s+by|sort(?:ed)?\s+by)\s+(\S+)\s+(?:desc|descending)",
        "en",
        "DESC",
    ),
    # English — ASC default (negative lookahead to avoid consuming DESC)
    (
        r"\b(?:order(?:ed)?\s+by|sort(?:ed)?\s+by)\s+(\S+)(?!\s*(?:desc|descending|asc|ascending))",
        "en",
        None,
    ),
    # English — superlative
    (r"\b(?:highest|maximum|most|greatest)\s+(\S+)", "en", "DESC"),
    (r"\b(?:lowest|minimum|least)\s+(\S+)", "en", "ASC"),
]

# ── Table patterns ─────────────────────────────────────────────────────
_TABLE_PATTERNS: list[tuple[str, str]] = [
    # Chinese — "从 X 表/中/里 (查询/获取/...)"
    (r"从\s*(\S+?)\s*(?:表|中|里)", "zh"),
    # Chinese — "X表" with common verb/preposition prefix skipped
    (r"(?:查询|查找|获取|读取|统计|分析|关联|从|在|的)?(\S{1,4})表(?:格|中|里|的)?", "zh"),
    # English
    (r"\b(?:from|table)\s+(\S+)", "en"),
    (r"\bthe\s+(\S+)\s+table\b", "en"),
]

# ── Entity patterns ────────────────────────────────────────────────────
_ENTITY_PATTERNS: list[tuple[str, str, str]] = [
    # (pattern, language, entity_type)
    (r"""[「『"']([^'"「」『』]+)['"」』]""", "both", "value"),
    (r"['\"]([^'\"]+)['\"]", "both", "value"),
]


class SQRBuilder:
    """Assemble an ``SQR`` from pre-parsed NL components.

    Extracts limit, order specifications, table references, and entities
    from raw NL text using regex heuristics.  No LLM calls — pure rules.

    Usage::

        builder = SQRBuilder()
        sqr = builder.build(
            nl_text="查询前10个订单按金额降序",
            language="zh",
            intent=IntentType.SELECT,
            time_range=None,
            ambiguities=[],
        )
    """

    def __init__(self) -> None:
        self._limit = [(re.compile(p, re.IGNORECASE), lang) for p, lang in _LIMIT_PATTERNS]
        self._order: list[tuple[re.Pattern[str], str, str | None]] = [
            (re.compile(p, re.IGNORECASE), lang, d) for p, lang, d in _ORDER_PATTERNS
        ]
        self._table = [(re.compile(p, re.IGNORECASE), lang) for p, lang in _TABLE_PATTERNS]
        self._entity = [
            (re.compile(p, re.IGNORECASE), lang, etype)
            for p, lang, etype in _ENTITY_PATTERNS
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        nl_text: str,
        language: Language,
        intent: IntentType,
        time_range: TimeRange | None = None,
        ambiguities: list[Ambiguity] | None = None,
        entities: list[Entity] | None = None,
        context: dict | None = None,  # noqa: ARG002  reserved for Phase 3
    ) -> SQR:
        """Assemble the final SQR from all parsed components.

        Args:
            nl_text: Raw NL input string.
            language: Detected language (``"zh"``, ``"en"``, ``"mixed"``).
            intent: Classified intent type.
            time_range: Extracted time range (may be ``None``).
            ambiguities: Detected ambiguities (may be ``None``).
            entities: Pre-extracted entities (may be ``None``); builder
                      will also run its own entity extraction pass.
            context: Reserved for Phase 3.

        Returns:
            A fully populated ``SQR`` dataclass instance.
        """
        limit = self._extract_limit(nl_text, language)
        order_by = self._extract_order(nl_text, language)
        target_tables = self._extract_tables(nl_text, language)
        extracted_entities = self._extract_entities(nl_text, language)

        if entities:
            extracted_entities = entities + extracted_entities

        amb_list = list(ambiguities) if ambiguities else []
        confidence = _compute_confidence(language, intent, time_range, amb_list)

        return SQR(
            raw_text=nl_text,
            language=language,
            intent=intent,
            entities=extracted_entities,
            time_range=time_range,
            target_tables=target_tables,
            target_columns=[],
            conditions=[],
            order_by=order_by,
            limit=limit,
            ambiguities=amb_list,
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # Extractors
    # ------------------------------------------------------------------

    def _extract_limit(self, text: str, lang: str) -> int | None:
        """Extract LIMIT value from NL text.

        Returns the smallest limit found if multiple patterns match
        (the most conservative choice).
        """
        candidates: list[int] = []
        for pattern, pat_lang in self._limit:
            if _lang_ok(pat_lang, lang):
                for m in pattern.finditer(text):
                    candidates.append(int(m.group(1)))
        return min(candidates) if candidates else None

    def _extract_order(self, text: str, lang: str) -> list[OrderSpec]:
        """Extract ORDER BY specifications from NL text.

        Deduplicates: if the same column appears in multiple matches
        the more specific (direction-explicit) match wins.  When both
        are explicit the first match is kept.
        """
        # Store original direction value: None = unspecified, str = explicit
        seen: dict[str, str | None] = {}
        for pattern, pat_lang, direction in self._order:
            if not _lang_ok(pat_lang, lang):
                continue
            for m in pattern.finditer(text):
                col = m.group(1).strip().rstrip(",.;，。；")
                if not col:
                    continue
                if col in seen:
                    # Override only when new match has explicit direction
                    # and existing match does not.
                    if direction is not None and seen[col] is None:
                        seen[col] = direction
                else:
                    seen[col] = direction

        return [
            OrderSpec(column=col, direction=d if d is not None else "ASC")  # type: ignore[arg-type]
            for col, d in seen.items()
        ]

    def _extract_tables(self, text: str, lang: str) -> list[str]:
        """Extract table name references from NL text."""
        tables: list[str] = []
        for pattern, pat_lang in self._table:
            if _lang_ok(pat_lang, lang):
                for m in pattern.finditer(text):
                    name = m.group(1).strip().rstrip(",.;，。；")
                    if name and name not in tables:
                        tables.append(name)
        return tables

    def _extract_entities(self, text: str, lang: str) -> list[Entity]:
        """Extract named entities (quoted strings, IDs, etc.) from NL text."""
        results: list[Entity] = []
        for pattern, pat_lang, etype in self._entity:
            if _lang_ok(pat_lang, lang):
                for m in pattern.finditer(text):
                    value = m.group(1).strip()
                    if value:
                        results.append(
                            Entity(
                                name=value,
                                type=etype,
                                normalized=value.lower(),
                                confidence=0.8,
                                start_pos=m.start(1),
                                end_pos=m.end(1),
                            )
                        )
        return results


# ── Module-private helpers ─────────────────────────────────────────────


def _compute_confidence(
    language: str,
    intent: IntentType,
    time_range: TimeRange | None,
    ambiguities: list[Ambiguity],
) -> float:
    """Compute a rough confidence score for the parsed SQR.

    Heuristic — not a statistical measure. Guides downstream decisions
    (e.g., whether to ask for clarification before generating SQL).
    """
    score = 0.5
    if language != "mixed":
        score += 0.1
    if intent != IntentType.UNKNOWN:
        score += 0.15
    if time_range is not None:
        score += 0.15
    score -= 0.05 * len(ambiguities)
    return max(0.0, min(1.0, score))
