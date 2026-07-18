"""Prompt builder — multi-level LLM prompt assembly with token budget management.

See SPEC §4.7.2 and implementation-plan §4.7.2 for the full design.

Four prompt levels (L1–L4) target different query complexities, each with
its own template, section budget, and injection strategy.  The builder
selects the level automatically from SQR complexity signals.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from app.models.domain import DomainConfig
from app.models.query import SQR, IntentType, QueryPair
from app.models.schema import SchemaSnapshot

# ── Prompt Level ────────────────────────────────────────────────────────


class PromptLevel(Enum):
    """Prompt complexity tier — matched to SQR complexity."""

    L1_SIMPLE = "simple"          # Single-table, simple WHERE, ~1500 tokens
    L2_STANDARD = "standard"      # Multi-table JOIN + aggregation, ~3500 tokens
    L3_COMPLEX = "complex"        # CTE + window functions, ~5500 tokens
    L4_SELF_HEAL = "self_heal"   # Error correction retry, ~2000 tokens


# ── Default token budgets per level ─────────────────────────────────────

DEFAULT_BUDGETS: dict[PromptLevel, int] = {
    PromptLevel.L1_SIMPLE: 5000,
    PromptLevel.L2_STANDARD: 4000,
    PromptLevel.L3_COMPLEX: 6000,
    PromptLevel.L4_SELF_HEAL: 2000,
}

# ── Section budgets per level (fractions of total) ──────────────────────
# Values are fractions of total budget.  System instructions always come
# first; the remaining budget is divided among sections by priority.

SECTION_BUDGETS: dict[PromptLevel, dict[str, float]] = {
    PromptLevel.L1_SIMPLE: {
        "system": 0.08,
        "schema": 0.30,
        "multi_table": 0.22,
        "glossary": 0.12,
        "rules": 0.10,
        "question": 0.18,
    },
    PromptLevel.L2_STANDARD: {
        "system": 0.08,
        "schema": 0.28,
        "multi_table": 0.18,
        "glossary": 0.12,
        "rules": 0.10,
        "history": 0.09,
        "examples": 0.07,
        "question": 0.08,
    },
    PromptLevel.L3_COMPLEX: {
        "system": 0.06,
        "schema": 0.26,
        "multi_table": 0.18,
        "glossary": 0.10,
        "rules": 0.10,
        "history": 0.10,
        "examples": 0.14,
        "question": 0.08,
    },
    PromptLevel.L4_SELF_HEAL: {
        "system": 0.25,
        "error_context": 0.35,
        "schema": 0.25,
        "question": 0.15,
    },
}

# Budget for no-schema domain-first mode
NO_SCHEMA_BUDGETS: dict[str, float] = {
    "system": 0.10,
    "glossary": 0.45,
    "rules": 0.20,
    "rag": 0.15,
    "question": 0.10,
}

# ── L1 Simple template ──────────────────────────────────────────────────

L1_TEMPLATE = """\
You are a {dialect} SQL expert. Generate a correct, read-only SELECT statement
for the user's question.

CRITICAL RULES — follow these EXACTLY or the query will fail:
1. You MUST ONLY use table names and column names that appear in the
   Database Schema section below. DO NOT invent, guess, or translate names.
2. Before outputting, VERIFY that every table and column you reference
   exists in the schema. If a column doesn't exist, use a different
   approach — NEVER make up a column name.
3. Read the schema carefully — column names are listed under each table
   with the format: column_name: DATA_TYPE.
4. If you cannot find a column you need in the schema, DO NOT guess it.
   Instead, use a different query approach or note that the data is unavailable.
{multi_table}

## Database Schema
{schema}

## Business Glossary
{glossary}

## Business Rules
{rules}

## Question
{question}

Return ONLY the SQL query. No explanation, no markdown formatting."""

# ── L2 Standard template ────────────────────────────────────────────────

L2_TEMPLATE = """\
You are a {dialect} SQL expert. Generate a correct, read-only SELECT statement
for the user's question.

CRITICAL RULES — follow these EXACTLY or the query will fail:
1. You MUST ONLY use table names and column names that appear in the
   Database Schema section below. DO NOT invent, guess, or translate names.
2. Before outputting, VERIFY that every table and column you reference
   exists in the schema. If a column doesn't exist, use a different
   approach — NEVER make up a column name.
3. Use explicit JOIN syntax (INNER JOIN, LEFT JOIN, etc.)
4. Determine JOIN conditions from the FK relationships shown in the schema
   (columns marked [FK→(table, column)] should be joined to the referenced table)
5. Use table aliases for readability
6. Handle NULL values with COALESCE or IS NULL as appropriate
7. Apply correct aggregation functions (COUNT, SUM, AVG, etc.)
8. Filter with WHERE before aggregation; use HAVING for post-aggregation filters
9. Follow the business rules below when they apply
10. If you cannot find a column you need, DO NOT guess — use a different approach
{multi_table}

## Database Schema
{schema}

## Business Glossary
{glossary}

## Business Rules
{rules}

## Conversation History
{history}

## Similar Query Examples
{examples}

## Question
{question}

Return ONLY the SQL query. No explanation, no markdown formatting."""

# ── L3 Complex template ─────────────────────────────────────────────────

L3_TEMPLATE = """\
You are a {dialect} SQL expert. Generate a correct, read-only SELECT statement
for the user's question. You are working with a complex query that may require
CTEs, window functions, or subqueries.

CRITICAL RULES — follow these EXACTLY or the query will fail:
1. You MUST ONLY use table names and column names that appear in the
   Database Schema section below. DO NOT invent, guess, or translate names.
2. Before outputting, VERIFY that every table and column you reference
   exists in the schema. If a column doesn't exist, use a different
   approach — NEVER make up a column name.
3. If you cannot find a column you need, DO NOT guess — use a different approach

Guidelines:
- Use CTEs (WITH clause) for multi-step transformations
- Use window functions (ROW_NUMBER, RANK, LAG, LEAD, etc.) where appropriate
- Use explicit JOIN syntax with correct join conditions
- Determine JOIN conditions from the FK relationships shown in the schema
  (columns marked [FK→(table, column)] should be joined to the referenced table)
- Handle NULL values with COALESCE or IS NULL
- Apply correct aggregation with GROUP BY
- Use table aliases consistently
- Follow the business rules below when they apply
{multi_table}

## Database Schema
{schema}

## Business Glossary
{glossary}

## Business Rules
{rules}

## Conversation History
{history}

## Similar Query Examples
{examples}

## Question
{question}

Return ONLY the SQL query. No explanation, no markdown formatting."""

# ── L4 Self-heal template ───────────────────────────────────────────────

L4_TEMPLATE = """\
You are a {dialect} SQL expert. The previous SQL query failed. Analyze the
error and generate a corrected version.

## Error Information
{error_context}

## Database Schema (relevant tables)
{schema}

## Original Question
{question}

Return ONLY the corrected SQL query. No explanation, no markdown formatting."""

# ── No-Schema (Domain-First) template ───────────────────────────────────

NO_SCHEMA_TEMPLATE = """\
You are a {dialect} SQL expert. The user is NOT connected to a live database,
so you MUST use the business glossary below as your ONLY reference for table
and column names.

## Business Glossary (use these SQL mappings as table/column references)
{glossary}

## Business Rules (must follow these when applicable)
{rules}

## RAG Knowledge (indexed schema knowledge from previous connections)
{rag_context}

## Question
{question}

IMPORTANT:
- Use the glossary SQL expressions directly as your building blocks
- If the question matches a glossary term, use its exact SQL mapping (shown with →)
- You MUST generate a SQL query for ANY question — even if no glossary term matches.
- If no glossary term matches, use the RAG knowledge (if available) and your own
  understanding of the question's semantics to construct a reasonable SQL query.
- Make reasonable assumptions about table and column names based on the glossary
  patterns and the question's intent.
- Add a brief SQL comment explaining your assumptions.
- NEVER return "NO_GLOSSARY_MATCH" or refuse to generate SQL.

Return ONLY the SQL query (with comment). No explanation, no markdown formatting."""

# ── No-Schema + No-Domain fallback ──────────────────────────────────────

NO_CONTEXT_TEMPLATE = """\
The user asked: "{question}"

No database schema and no domain glossary are available. Please respond with:
"NO_CONTEXT: 请先连接数据库（左侧"数据库连接"面板）或配置领域知识（"领域知识"面板），
然后再进行查询。当前无法生成有效的 SQL 语句。"

Return ONLY that message."""

# ── RAG-Only (no DB, no domain, but has RAG knowledge) ─────────────────

RAG_ONLY_TEMPLATE = """\
You are a {dialect} SQL expert. The user is NOT connected to a live database and
no domain glossary is available, but RAG-indexed schema knowledge from previous
connections is available. Use this RAG knowledge as your ONLY reference for table
and column names.

## RAG Knowledge (indexed schema knowledge from previous connections)
{rag_context}

## Question
{question}

IMPORTANT:
- Use the RAG knowledge above as your primary reference for table and column names.
- Prefer tables and columns that appear in the RAG knowledge.
- You MUST generate a SQL query for ANY question — even if the RAG knowledge
  does not directly cover the query topic.
- If the RAG knowledge does not cover the query, make reasonable assumptions about
  table and column names based on the question's semantics.
- Add a brief SQL comment explaining your assumptions.
- NEVER return "NO_RAG_MATCH" or refuse to generate SQL.

Return ONLY the SQL query (with comment). No explanation, no markdown formatting."""


# ── LLM Auto-Generate (no DB, no domain, no RAG) ───────────────────────

LLM_AUTO_TEMPLATE = """\
You are a SQL expert. The user's question is not related to any connected database,
domain knowledge, or RAG knowledge base. Generate a SQL statement based on your
understanding of the question's semantics.

## Question
{question}

CRITICAL RULES:
- You MUST generate a SQL query for ANY question — even general knowledge questions.
- This SQL is auto-generated by the LLM and does NOT involve any connected database,
  domain knowledge, or RAG knowledge base.
- Use standard ANSI SQL syntax.
- Make reasonable assumptions about table and column names based on the question's
  semantics. For example, if the user asks about "countries", assume a table named
  "countries" with appropriate columns.
- Add a brief SQL comment at the beginning explaining your assumptions.
- NEVER return "NOT_A_QUERY" or refuse to generate SQL.
- If the question seems like general knowledge, still generate a SQL query that
  would retrieve the answer from a hypothetical table.

Return ONLY the SQL query (with comment). No explanation, no markdown formatting."""


# ── Dialect default ─────────────────────────────────────────────────────

DIALECT_DEFAULT = "ansi"


class PromptBuilder:
    """Multi-level prompt builder with automatic level selection and token budgeting.

    Usage::

        builder = PromptBuilder()
        prompt = builder.build(
            sqr=parsed_sqr,
            schema=db_schema,
            domain=ecommerce_config,
            history=[],
            similar_pairs=[],
            dialect="postgresql",
        )
    """

    def __init__(
        self,
        token_budgets: dict[PromptLevel, int] | None = None,
        *,
        dialect: str = DIALECT_DEFAULT,
    ) -> None:
        self.token_budgets = token_budgets or dict(DEFAULT_BUDGETS)
        self.dialect = dialect

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        sqr: SQR,
        schema: SchemaSnapshot | None = None,
        domain: DomainConfig | None = None,
        history: list[Any] | None = None,
        similar_pairs: list[QueryPair] | None = None,
        *,
        dialect: str | None = None,
        error_context: str | None = None,
        rag_context: str | None = None,
        multi_table_hint: dict[str, Any] | None = None,
        database_name: str = "",
    ) -> str:
        """Assemble a level-appropriate prompt from the given components.

        Args:
            sqr: The parsed Structured Query Representation.
            schema: Optional database schema snapshot.
            domain: Optional domain configuration (glossary + rules).
            history: Optional conversation turns for context.
            similar_pairs: Optional few-shot NL→SQL examples.
            dialect: SQL dialect override (default: ``"ansi"``).
            error_context: Required for L4_SELF_HEAL — the error from
                           the failed execution.
            rag_context: Optional RAG knowledge text for no-schema mode.
            multi_table_hint: Optional hint from schema linking listing
                              tables that independently contain matching
                              columns.  When *has_alternatives* is True,
                              the prompt instructs the LLM to generate
                              one SQL per table group.
            database_name: Optional name of the target database.

        Returns:
            A complete prompt string ready for the LLM.
        """
        used_dialect = dialect or self.dialect

        # ── No-schema path: choose strategy based on available context ─
        if schema is None:
            # L4 self-heal with error_context — use existing L4 template
            if error_context is not None:
                pass  # fall through to normal level selection (forces L4)
            elif domain is not None:
                # Domain-first: glossary SQL mappings as primary reference
                return self._build_no_schema(sqr, domain, used_dialect, rag_context)
            elif rag_context is not None:
                # RAG-only: no domain but has RAG knowledge
                return self._build_no_schema(sqr, None, used_dialect, rag_context)
            else:
                # No schema, no domain, no RAG — LLM auto-generate
                return LLM_AUTO_TEMPLATE.format(question=sqr.raw_text)

        level = self.select_level(sqr, error_context is not None)
        budget = self.token_budgets.get(level, 1500)
        sections = SECTION_BUDGETS.get(level, SECTION_BUDGETS[PromptLevel.L1_SIMPLE])

        # Compute per-section token caps
        caps = {k: int(v * budget) for k, v in sections.items()}

        # ── Render each section ──────────────────────────────────
        schema_text = self._render_schema(
            schema, caps.get("schema", 500),
            multi_table_hint=multi_table_hint,
            database_name=database_name,
        )
        glossary_text = self._render_glossary(domain, sqr, caps.get("glossary", 300))
        rules_text = self._render_rules(domain, sqr, caps.get("rules", 300))
        history_text = self._render_history(history, caps.get("history", 300))
        examples_text = self._render_examples(
            similar_pairs, caps.get("examples", 300)
        )
        error_text = self._render_error_context(
            error_context, caps.get("error_context", 500)
        )
        multi_table_text = self._render_multi_table_hint(
            multi_table_hint, caps.get("multi_table", 800),
            schema=schema,
        )

        # ── Heuristic multi-table: when schema has 2+ tables, always ─
        # encourage the LLM to consider multiple independent tables.
        if not multi_table_text and schema and len(schema.tables) >= 2:
            multi_table_text = self._render_heuristic_multitable(schema, caps.get("multi_table", 800))

        # ── Select and fill template ─────────────────────────────
        template = self._select_template(level)
        prompt = template.format(
            dialect=used_dialect,
            schema=schema_text or "(no schema provided)",
            glossary=glossary_text or "(no glossary)",
            rules=rules_text or "(no business rules)",
            history=history_text or "(no conversation history)",
            examples=examples_text or "(no examples)",
            question=sqr.raw_text,
            error_context=error_text or "",
            multi_table=multi_table_text or "",
        )

        # ── Enforce total budget ─────────────────────────────────
        return self._trim_to_budget(prompt, budget)

    def select_level(self, sqr: SQR, is_retry: bool = False) -> PromptLevel:
        """Choose the prompt level based on SQR complexity signals.

        Complexity scoring:
            - ≥ 2 entities        +1  (multiple entities = potential multi-table)
            - JOIN intent         +2  (strong signal — forces L2 minimum)
            - AGGREGATE intent    +1  (often needs GROUP BY + JOIN)
            - FUNNEL intent       +3  (complex multi-step logic)
            - aggregation ambiguity +1
            - has time_range      +1

        ========  ============
        Score      Level
        ========  ============
        ≤ 0        L1_SIMPLE   (single-table, no aggregation)
        1–3        L2_STANDARD (multi-table, aggregation, moderate complexity)
        ≥ 4        L3_COMPLEX  (CTEs, window functions, heavy multi-step)
        ========  ============

        JOIN intent always guarantees at least L2 (score ≥ 2).
        When *is_retry* is True, L4_SELF_HEAL is returned regardless.
        """
        if is_retry:
            return PromptLevel.L4_SELF_HEAL

        complexity = 0
        if len(sqr.entities) >= 2:
            complexity += 1
        if sqr.intent == IntentType.JOIN:
            complexity += 2  # was +1 — JOIN alone now guarantees L2
        if sqr.intent == IntentType.AGGREGATE:
            complexity += 1  # AGGREGATE often needs GROUP BY + multi-table
        if sqr.intent == IntentType.FUNNEL:
            complexity += 3  # was +2
        # Check for aggregation ambiguity
        if any(a.aspect == "aggregation" for a in sqr.ambiguities):
            complexity += 1
        if sqr.time_range is not None:
            complexity += 1

        if complexity <= 0:
            return PromptLevel.L1_SIMPLE
        if complexity <= 3:
            return PromptLevel.L2_STANDARD
        return PromptLevel.L3_COMPLEX

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token count estimate.

        English / ASCII: ~4 characters per token.
        Chinese / CJK: ~1.5 characters per token.

        This is a heuristic — not exact.  For precise counts, use the
        model's tokenizer (e.g., ``tiktoken``).  The 4-char rule is a
        common rule-of-thumb that slightly overestimates for English
        (avoiding budget overruns) and is accurate enough for CJK.
        """
        if not text:
            return 0
        en_chars = sum(1 for c in text if c.isascii() and not c.isspace())
        zh_chars = len(text) - en_chars
        return int(en_chars / 4 + zh_chars / 1.5)

    # ------------------------------------------------------------------
    # Template selection
    # ------------------------------------------------------------------

    @staticmethod
    def _select_template(level: PromptLevel) -> str:
        """Return the template string for *level*."""
        if level == PromptLevel.L1_SIMPLE:
            return L1_TEMPLATE
        if level == PromptLevel.L2_STANDARD:
            return L2_TEMPLATE
        if level == PromptLevel.L3_COMPLEX:
            return L3_TEMPLATE
        if level == PromptLevel.L4_SELF_HEAL:
            return L4_TEMPLATE
        return L1_TEMPLATE

    # ------------------------------------------------------------------
    # No-schema (domain-first) builder
    # ------------------------------------------------------------------

    def _build_no_schema(
        self,
        sqr: SQR,
        domain: DomainConfig | None,
        dialect: str,
        rag_context: str | None = None,
    ) -> str:
        """Build a no-schema prompt based on available context.

        Priority:
        1. *domain* available → glossary + rules + RAG (NO_SCHEMA_TEMPLATE)
        2. *domain* is None but *rag_context* available → RAG-only (RAG_ONLY_TEMPLATE)
        3. Neither domain nor RAG → tell user to set up (NO_CONTEXT_TEMPLATE)
        """
        budget = 2000  # generous budget for domain content
        caps = {k: int(v * budget) for k, v in NO_SCHEMA_BUDGETS.items()}

        if domain is not None:
            glossary_text = self._render_glossary(domain, sqr, caps.get("glossary", 900))
            rules_text = self._render_rules(domain, sqr, caps.get("rules", 400))

            rag_text = rag_context or "(no RAG knowledge available)"

            return NO_SCHEMA_TEMPLATE.format(
                dialect=dialect,
                glossary=glossary_text or "(no matching glossary terms for this question)",
                rules=rules_text or "(no matching business rules for this question)",
                rag_context=rag_text,
                question=sqr.raw_text,
            )

        # No domain — check if RAG knowledge is available
        if rag_context:
            return RAG_ONLY_TEMPLATE.format(
                dialect=dialect,
                rag_context=rag_context,
                question=sqr.raw_text,
            )

        # Neither schema, domain, nor RAG — tell user to set up first
        return NO_CONTEXT_TEMPLATE.format(question=sqr.raw_text)

    # ------------------------------------------------------------------
    # Section renderers
    # ------------------------------------------------------------------

    @staticmethod
    def _render_schema(
        schema: SchemaSnapshot | None, cap_tokens: int,
        multi_table_hint: dict[str, Any] | None = None,
        database_name: str = "",
    ) -> str:
        """Render the schema section within *cap_tokens*.

        Uses ``SchemaSnapshot.format_for_llm()`` and trims tables if needed.
        When *multi_table_hint* provides independent_tables, those tables are
        prioritized and shown first.
        *database_name* is prepended as a header to identify the target database.
        """
        if schema is None:
            return ""

        # ── Prioritize tables from multi_table_hint ──────────────
        prioritized: list[str] = []
        if multi_table_hint and multi_table_hint.get("independent_tables"):
            prioritized = multi_table_hint["independent_tables"]

        # Build a reordered table list: prioritized first, then rest
        all_table_names = sorted(schema.tables.keys())
        reordered = prioritized + [t for t in all_table_names if t not in prioritized]

        # Start with all tables, trim if over budget
        max_tables = len(reordered)
        for n in range(max_tables, 0, -1):
            text = schema.format_for_llm(table_limit=n, table_order=reordered[:n])
            if PromptBuilder.estimate_tokens(text) <= cap_tokens:
                if database_name:
                    text = f"-- Target Database: {database_name}\n{text}"
                return text

        result = schema.format_for_llm(table_limit=1, table_order=reordered[:1])
        if database_name:
            result = f"-- Target Database: {database_name}\n{result}"
        return result

    @staticmethod
    def _render_glossary(
        domain: DomainConfig | None, sqr: SQR, cap_tokens: int
    ) -> str:
        """Render relevant glossary terms within *cap_tokens*.

        Filters terms by keyword match against the NL text and SQR entities,
        then trims to fit the budget.
        """
        if domain is None or not domain.glossary:
            return ""

        # Select relevant terms — those whose term (zh or en) appears in
        # the NL text or matches an SQR entity name.
        nl_lower = sqr.raw_text.lower()
        entity_names = {e.name.lower() for e in sqr.entities}
        relevant: list[str] = []
        for term in domain.glossary:
            term_lower = term.term.lower()
            term_en_lower = term.term_en.lower() if term.term_en else ""
            if (
                term_lower in nl_lower
                or (term_en_lower and term_en_lower in nl_lower)
                or term_lower in entity_names
                or (term_en_lower and term_en_lower in entity_names)
            ):
                line = f"  - {term.term}"
                if term.term_en:
                    line += f" ({term.term_en})"
                if term.description:
                    line += f": {term.description}"
                if term.mapping:
                    line += f"  → {term.mapping.expression}"
                relevant.append(line)

        if not relevant:
            return ""

        # Build from most relevant, trim to budget
        lines: list[str] = []
        for entry in relevant:
            candidate = "\n".join(lines + [entry])
            if PromptBuilder.estimate_tokens(candidate) > cap_tokens:
                break
            lines.append(entry)

        return "\n".join(lines) if lines else ""

    @staticmethod
    def _render_rules(
        domain: DomainConfig | None, sqr: SQR, cap_tokens: int
    ) -> str:
        """Render relevant business rules within *cap_tokens*.

        Filters rules by pattern regex against the NL query text so only
        rules that match the user's question are included in the prompt.
        """
        if domain is None or not domain.rules:
            return ""

        import re as _re

        lines: list[str] = []
        for rule in domain.rules:
            # Filter by pattern regex (skip rules that don't match query text)
            if rule.pattern:
                try:
                    if not _re.search(rule.pattern, sqr.raw_text, _re.IGNORECASE):
                        continue
                except _re.error:
                    pass  # include rule if pattern is invalid regex

            entry = f"  [{rule.id}] {rule.description}"
            if rule.sql_template:
                entry += f"\n    SQL template: {rule.sql_template}"
            candidate = "\n".join(lines + [entry])
            if PromptBuilder.estimate_tokens(candidate) > cap_tokens:
                break
            lines.append(entry)

        return "\n".join(lines) if lines else ""

    @staticmethod
    def _render_history(
        history: list[Any] | None, cap_tokens: int
    ) -> str:
        """Render conversation history within *cap_tokens*.

        Each turn is rendered as "Q: ... A: ...".  Recent turns are
        prioritized (appended last = rendered last).
        """
        if not history:
            return ""

        entries: list[str] = []
        for turn in reversed(history):  # newest first — truncate oldest
            # Support both ConversationTurn objects and plain dicts
            if hasattr(turn, "nl_input"):
                q = getattr(turn, "nl_input", "")
            elif isinstance(turn, dict):
                q = turn.get("nl_input", turn.get("question", ""))
            else:
                q = str(turn)

            if hasattr(turn, "selected_candidate_id"):
                a = getattr(turn, "selected_candidate_id", "")
            elif isinstance(turn, dict):
                a = turn.get("sql", turn.get("answer", ""))
            else:
                a = ""

            entry = f"Q: {q}"
            if a:
                entry += f"\nA: {a}"
            entries.insert(0, entry)  # maintain chronological order

            candidate = "\n---\n".join(entries)
            if PromptBuilder.estimate_tokens(candidate) > cap_tokens:
                entries.pop(0)
                break

        return "\n---\n".join(entries) if entries else ""

    @staticmethod
    def _render_examples(
        pairs: list[QueryPair] | None, cap_tokens: int
    ) -> str:
        """Render few-shot NL→SQL examples within *cap_tokens*.

        Higher similarity pairs are prioritized (sorted descending).
        """
        if not pairs:
            return ""

        # Sort by similarity (descending)
        sorted_pairs = sorted(pairs, key=lambda p: p.similarity, reverse=True)

        entries: list[str] = []
        for pair in sorted_pairs:
            entry = f"Q: {pair.nl_text}\nSQL: {pair.sql_text}"
            candidate = "\n---\n".join(entries + [entry])
            if PromptBuilder.estimate_tokens(candidate) > cap_tokens:
                break
            entries.append(entry)

        return "\n---\n".join(entries) if entries else ""

    @staticmethod
    def _render_error_context(
        error_context: str | None, cap_tokens: int
    ) -> str:
        """Render error context for self-heal prompts."""
        if not error_context:
            return ""
        if PromptBuilder.estimate_tokens(error_context) <= cap_tokens:
            return error_context
        # Truncate to cap
        return error_context[: cap_tokens * 4] + "..."

    @staticmethod
    def _render_multi_table_hint(
        hint: dict[str, Any] | None, cap_tokens: int,
        schema: SchemaSnapshot | None = None,
    ) -> str:
        """Render the multi-table exploration instruction.

        When *hint* indicates that the same column exists in multiple
        independent tables (e.g. "username" in users, operation_logs),
        instruct the LLM to generate one SQL per table group, separated
        by ``---ALTERNATIVE---``.

        Also includes an explicit column-name mapping so the LLM uses
        the EXACT column names from the schema (e.g. it must use
        ``username`` not ``name``).

        When *schema* is provided, the per-table column listing is
        included so the LLM can see ALL available columns for each
        independent table — preventing it from guessing wrong column
        names for non-matched columns.
        """
        if not hint:
            return ""

        independent = hint.get("independent_tables", [])
        column_groups = hint.get("column_groups", {})
        has_alternatives = hint.get("has_alternatives", False)

        # ── Build per-table column listing (CRITICAL for accuracy) ──
        # For each independent table, list ALL its columns so the LLM
        # knows exactly what's available.  This prevents it from
        # hallucinating column names that don't exist.
        table_col_lines: list[str] = []
        if schema is not None:
            for tbl_name in sorted(independent):
                tbl = schema.tables.get(tbl_name)
                if tbl is None:
                    continue
                col_names = [c.name for c in tbl.columns]
                table_col_lines.append(
                    f"  - Table \"{tbl_name}\": columns = [{', '.join(col_names)}]"
                )
        table_columns_text = "\n".join(table_col_lines) if table_col_lines else ""

        # ── Build column mapping section (always include if available) ──
        # This tells the LLM the EXACT column names to use, preventing
        # it from guessing "name" when the actual column is "username".
        col_mapping_lines: list[str] = []
        for col_name, cg in column_groups.items():
            tables = cg.get("all_tables", [])
            if tables:
                col_mapping_lines.append(
                    f"  - Use column \"{col_name}\" (found in: {', '.join(tables)})"
                )

        col_mapping = "\n".join(col_mapping_lines) if col_mapping_lines else ""

        # ── Multi-table alternatives section ──
        multi_table_lines: list[str] = []
        if has_alternatives and len(independent) >= 2:
            for col_name, cg in column_groups.items():
                tables = cg.get("all_tables", [])
                if len(tables) > 1:
                    multi_table_lines.append(
                        f"  - Column \"{col_name}\" exists in: {', '.join(tables)}"
                    )

        multi_table_summary = "\n".join(multi_table_lines) if multi_table_lines else ""

        # ── Assemble the text ──
        parts: list[str] = []

        # Per-table column listing (most important for accuracy)
        if table_columns_text:
            parts.append(f"""## Per-Table Column Reference — CRITICAL
Below is the EXACT list of columns for EACH table you may query.
You MUST ONLY use column names from this list — do NOT invent names.

{table_columns_text}""")

        if col_mapping:
            parts.append(f"""## Column Mapping — CRITICAL
The user's question has been matched to these EXACT database columns.
You MUST use ONLY these column names in your SQL — do NOT substitute,
translate, or guess alternative names:

{col_mapping}""")

        if has_alternatives and len(independent) >= 2:
            if multi_table_summary:
                parts.append(f"""## Multi-Table Alternatives — IMPORTANT
The question can be answered using DIFFERENT independent tables because
the same columns exist in multiple tables:

{multi_table_summary}

**INSTRUCTIONS:**
- Generate a SEPARATE SQL query for EACH independent table that can answer the question.
- You MUST generate SQL for EVERY table listed in the Per-Table Column Reference above
  if that table contains the relevant data — do NOT skip any.
- Tables that require JOINs to answer the question should be grouped into ONE SQL.
- Separate each SQL query with the exact marker: ---ALTERNATIVE---
- Return at most one SQL per independent table group.
- Do NOT return duplicate SQLs — each alternative must use different tables.
- Use the EXACT column names from the Per-Table Column Reference above.""")
            else:
                parts.append(f"""## Multi-Table Alternatives — IMPORTANT
The same kind of data may exist in DIFFERENT independent tables.
Candidate tables: {', '.join(independent)}

**INSTRUCTIONS:**
- Check EACH of the candidate tables above — if the table can answer the question, generate a SQL query for it.
- You MUST generate SQL for EVERY table that contains the relevant data — do NOT skip any.
- If MULTIPLE tables can independently answer the question, generate a SEPARATE SQL query for EACH one.
- Separate each SQL query with the exact marker: ---ALTERNATIVE---
- Do NOT return duplicate SQLs — each alternative must use different tables.
- Use the EXACT column names from the Per-Table Column Reference above.""")

        text = "\n\n".join(parts)
        if not text:
            return ""

        if PromptBuilder.estimate_tokens(text) > cap_tokens:
            # Compact fallback — keep the most essential parts
            compact_parts = []
            if table_col_lines:
                compact_parts.append("EXACT table columns:\n" + "\n".join(table_col_lines))
            if col_mapping_lines:
                compact_parts.append("Use EXACT columns:\n" + "\n".join(col_mapping_lines))
            if has_alternatives and len(independent) >= 2:
                compact_parts.append(f"Generate ONE SQL per table: {', '.join(independent)}. Separate with: ---ALTERNATIVE---")
            short_text = "\n".join(compact_parts)
            if PromptBuilder.estimate_tokens(short_text) <= cap_tokens:
                return short_text
            return ""

        return text

    @staticmethod
    def _render_heuristic_multitable(
        schema: SchemaSnapshot, cap_tokens: int
    ) -> str:
        """Generate a heuristic multi-table instruction when schema has many tables.

        This is used as a fallback when schema linking didn't explicitly
        detect multi-table alternatives (e.g. Chinese NL terms don't match
        English table/column names).  It encourages the LLM to scan the
        table list and generate alternatives for each relevant table.
        """
        if schema is None or len(schema.tables) < 2:
            return ""

        table_names = sorted(schema.tables.keys())
        table_list = ", ".join(table_names)

        text = f"""## Multi-Table Check — IMPORTANT
The database has {len(table_names)} tables: {table_list}

**INSTRUCTIONS:**
- First, identify which tables could answer the user's question.
- If MULTIPLE independent tables can answer the question (e.g. different
  tables that each contain the requested column), generate a SEPARATE SQL
  query for EACH such table.
- If the question requires JOINing multiple tables, group them into ONE SQL.
- Separate each SQL query with the exact marker: ---ALTERNATIVE---
- Do NOT return duplicate SQLs — each alternative must use different tables.
- Match column names exactly as shown in the schema."""

        if PromptBuilder.estimate_tokens(text) > cap_tokens:
            short_text = f"## Multi-Table Check\nThe database has tables: {table_list}\nIf multiple tables can answer the question, generate ONE SQL per table. Separate with: ---ALTERNATIVE---"
            if PromptBuilder.estimate_tokens(short_text) <= cap_tokens:
                return short_text
            return ""

        return text

    @staticmethod
    def _trim_to_budget(prompt: str, budget: int) -> str:
        """Ensure *prompt* fits within *budget* tokens.

        If the prompt exceeds the budget, sections are progressively
        collapsed.  As a last resort, the prompt is truncated.
        """
        if PromptBuilder.estimate_tokens(prompt) <= budget:
            return prompt
        # Crude truncation — in practice, section-level trimming
        # already keeps us under budget in almost all cases.
        char_limit = budget * 4  # conservative: use English rule
        return prompt[:char_limit] + "\n\n-- [prompt truncated to budget]"
