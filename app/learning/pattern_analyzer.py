"""Pattern analyzer — cluster SQL edits and extract candidate business rules.

See SPEC §4.6.4 (pattern analysis & rule extraction) and §5.3 (Phase 5 roadmap).

The analyzer is **rule-based** (not LLM-driven) for determinism and speed.
It parses SQL diffs into normalized operations, groups similar edits, ranks
them by frequency, and produces :class:`CandidateRule` instances for human
review.

Usage::

    from app.learning.pattern_analyzer import PatternAnalyzer
    from app.learning.feedback_collector import feedback_collector

    analyzer = PatternAnalyzer(feedback_collector)
    candidates = analyzer.analyze(domain="ecommerce", window_days=30)
    # → [CandidateRule(rule_id="cr_001", pattern_description="添加 WHERE status ...")]
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

# ── CandidateRule ─────────────────────────────────────────────────────────


@dataclass
class CandidateRule:
    """A candidate business rule extracted from user edit patterns.

    Attributes:
        rule_id: Unique identifier (``cr_<uuid_hex>``).
        domain_id: The domain this rule belongs to.
        pattern_description: Human-readable description (Chinese or English).
        suggested_rule_name: Proposed name for the rule.
        suggested_pattern: Regex pattern for automatic matching.
        suggested_enforcement: Key-value directives for SQL generation.
        occurrence_count: How many times this pattern was observed.
        supporting_query_ids: Feedback record IDs that support this rule.
        confidence: 0.0–1.0 score based on frequency and consistency.
        status: ``pending_review``, ``approved``, or ``rejected``.
        created_at: ISO-8601 timestamp.
    """

    rule_id: str = field(default_factory=lambda: f"cr_{uuid4().hex[:8]}")
    domain_id: str = ""
    pattern_description: str = ""
    suggested_rule_name: str = ""
    suggested_pattern: str = ""
    sql_template: str = ""
    suggested_enforcement: dict[str, str] = field(default_factory=dict)
    occurrence_count: int = 0
    supporting_query_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "pending_review"
    created_at: str = ""


# ── diff parsing helpers ──────────────────────────────────────────────────


def _strip_sql_comments(sql: str) -> str:
    """Strip SQL comments to focus diff analysis on actual SQL changes.

    Removes:
    - Line comments: ``-- ...`` (until end of line)
    - Block comments: ``/* ... */``
    - Leading/trailing whitespace per line
    - Empty lines
    """
    # Remove block comments /* ... */
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    # Remove line comments -- ... (but not within strings)
    lines = []
    for line in sql.splitlines():
        # Simple heuristic: strip everything after -- that's not in a string
        # This handles the common case; edge cases with -- in strings are rare
        # in generated SQL
        stripped = re.sub(r'--.*$', '', line).strip()
        if stripped:
            lines.append(stripped)
    return '\n'.join(lines)


def _parse_diff_operations(
    sql_before: str, sql_after: str
) -> list[dict[str, Any]]:
    """Extract structured operations from a before/after SQL diff.

    Strips SQL comments first to focus on actual SQL statement changes,
    not comment modifications.

    Uses character-level comparison via ``difflib.SequenceMatcher`` for
    single-line SQL and line-level ``unified_diff`` for multi-line SQL.

    Args:
        sql_before: Original generated SQL.
        sql_after: User-edited SQL.

    Returns:
        List of operation dicts with keys: ``type``, ``keyword``,
        ``table``, ``column``, ``value``.
    """
    if not sql_before or not sql_after:
        return []

    # Strip comments to focus on actual SQL changes
    sql_before = _strip_sql_comments(sql_before)
    sql_after = _strip_sql_comments(sql_after)

    if sql_before == sql_after:
        return []

    # Detect whether this is single-line or multi-line
    before_lines = sql_before.splitlines()
    after_lines = sql_after.splitlines()
    is_multiline = len(before_lines) > 1 or len(after_lines) > 1

    ops: list[dict[str, Any]] = []

    if is_multiline:
        diff = list(
            difflib.unified_diff(
                before_lines, after_lines, lineterm="",
            )
        )
        for line in diff:
            if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
                continue
            op = _classify_diff_line(line)
            if op:
                ops.append(op)
    else:
        # Single-line SQL: use SequenceMatcher to find added/removed spans
        s = difflib.SequenceMatcher(None, sql_before, sql_after)
        for tag, i1, i2, j1, j2 in s.get_opcodes():
            if tag == "equal":
                continue
            elif tag == "insert":
                added = sql_after[j1:j2]
                op = _classify_text(added, "add")
                if op:
                    ops.append(op)
            elif tag == "delete":
                removed = sql_before[i1:i2]
                op = _classify_text(removed, "remove")
                if op:
                    ops.append(op)
            elif tag == "replace":
                removed = sql_before[i1:i2]
                added = sql_after[j1:j2]
                op_rem = _classify_text(removed, "remove")
                if op_rem:
                    ops.append(op_rem)
                op_add = _classify_text(added, "add")
                if op_add:
                    ops.append(op_add)

    return ops


def _classify_text(text: str, op_type: str) -> dict[str, Any] | None:
    """Classify a text fragment as a SQL operation type."""
    content = text.strip()
    if not content:
        return None

    op: dict[str, Any] = {"type": op_type, "raw_text": content}
    content_upper = content.upper()

    # Detect pattern type by keyword
    for kw in ["WHERE", "AND", "OR"]:
        if content_upper.startswith(kw):
            op["keyword"] = kw
            break
    else:
        if "JOIN" in content_upper:
            op["keyword"] = "JOIN"
        elif "GROUP BY" in content_upper:
            op["keyword"] = "GROUP_BY"
        elif "ORDER BY" in content_upper:
            op["keyword"] = "ORDER_BY"
        elif "HAVING" in content_upper:
            op["keyword"] = "HAVING"
        elif any(
            agg in content_upper
            for agg in ("SUM(", "COUNT(", "AVG(", "MAX(", "MIN(")
        ):
            op["keyword"] = "AGGREGATE"
        else:
            op["keyword"] = "OTHER"

    # Try to extract table.column or column reference
    col_match = re.search(
        r"(?:(\w+)\.)?(\w+)\s*(?:!=|>=|<=|<>|[=><])", content
    )
    if col_match:
        if col_match.group(1):
            op["table"] = col_match.group(1).lower()
        op["column"] = col_match.group(2).lower()

    return op


def _classify_diff_line(line: str) -> dict[str, Any] | None:
    """Classify a unified-diff line as a SQL operation."""
    op_type = "add" if line.startswith("+") else "remove"
    return _classify_text(line[1:], op_type)


# ── SQL validity & meaningful-edit checks ────────────────────────────────
#
# These helpers guard PatternAnalyzer against spurious "edits" that produce
# phantom rule candidates. The classic failure mode is test residue: a user
# (or an automated test) appends garbage characters like "111" or a typoed
# clause like "where uesr_id==1" to the end of an otherwise valid SQL
# statement. Without these guards, PatternAnalyzer treats the garbage as a
# real edit and generates a candidate rule describing it.


# Regex: detect obviously-invalid SQL constructs that would never appear in
# real SQL:
#   - Double equals (``==``) — valid in Python/JS, invalid in SQL
#   - Trailing digits/letters glued to a semicolon (``;123``, ``;abc``)
#   - Misspelled keywords (``wher``, ``selec``, ``form``)
_INVALID_SQL_PATTERNS = re.compile(
    r"==|"                               # SQL uses single = for comparison
    r";\s*[A-Za-z0-9]+\s*;|"            # "; garbage;" tail
    r";\s*\d+\s*$|"                      # "; 123" trailing digits
    r"\b(wher|selec|form|formm|updte|delte)\b",  # misspelled keywords
    re.IGNORECASE,
)


def _is_valid_sql(sql: str) -> bool:
    """Heuristic check: does *sql* look like parseable SQL?

    This intentionally uses a fast regex-based heuristic rather than a
    full sqlglot parse, because:
      1. sqlglot may not be installed in all environments.
      2. We only need to catch the common garbage patterns (typos,
         trailing digits, double-equals) — not validate full SQL syntax.
      3. Performance: PatternAnalyzer may call this on every record.
    """
    if not sql or not sql.strip():
        return False

    # Strip comments before checking (comments may contain prose that
    # matches our invalid-SQL patterns by coincidence).
    cleaned = _strip_sql_comments(sql).strip()
    if not cleaned:
        # The SQL is *all* comments — definitely not a real edited query.
        return False

    if _INVALID_SQL_PATTERNS.search(cleaned):
        return False

    return True


def _is_meaningful_edit(sql_before: str, sql_after: str) -> bool:
    """Check whether the difference between two SQL strings is a real edit.

    Returns False when the difference is trivial test residue (e.g., a
    few garbage characters appended to the end of an otherwise identical
    statement). This is used as a fallback when ``diff_count == 0`` but
    ``sql_gen != sql_final`` — a data-inconsistency signal.
    """
    if not sql_before or not sql_after:
        return False

    # Strip comments for fair comparison
    before = _strip_sql_comments(sql_before).strip()
    after = _strip_sql_comments(sql_after).strip()

    if before == after:
        return False

    # If sql_after starts with sql_before (tail-append pattern), the
    # appended content must contain a SQL keyword to count as a
    # meaningful edit.  This filters garbage like "tehstjsejr" or
    # "111" while allowing short real edits like "LIMIT 5".
    if after.startswith(before):
        tail = after[len(before):].strip()
        if not tail:
            return False
        sql_keywords = (
            "select", "where", "and", "or", "order", "group", "having",
            "limit", "join", "union", "insert", "update", "delete",
        )
        if not any(kw in tail.lower() for kw in sql_keywords):
            return False
        return True

    # If sql_before starts with sql_after (head-trimmed pattern), the
    # removal is always meaningful (user deleted a clause).
    if before.startswith(after):
        return True

    # Otherwise the strings diverge in the middle — treat as meaningful.
    return True


def _normalize_operation(op: dict[str, Any]) -> str:
    """Convert an operation dict to a normalized key for clustering.

    Example: ``"ADD_WHERE_orders_status"``.
    """
    parts = [op.get("type", "?"), op.get("keyword", "?"), op.get("table", ""), op.get("column", "")]
    return "_".join(p for p in parts if p).upper()


# ── PatternAnalyzer ───────────────────────────────────────────────────────


class PatternAnalyzer:
    """Analyze feedback records to discover edit patterns and extract rules.

    The analyzer reads from a :class:`FeedbackCollector`, parses SQL diffs,
    clusters similar operations, ranks clusters by frequency, and produces
    :class:`CandidateRule` instances for human review.
    """

    def __init__(self, feedback_collector: Any = None) -> None:
        """Initialise with an optional feedback collector.

        Args:
            feedback_collector: A :class:`FeedbackCollector` instance.
                If ``None``, lazily import the module-level singleton.
        """
        self._collector = feedback_collector

    @property
    def collector(self) -> Any:
        if self._collector is None:
            from app.learning.feedback_collector import feedback_collector

            self._collector = feedback_collector
        return self._collector

    # ── analyze ───────────────────────────────────────────────────────

    def analyze(
        self,
        domain: str = "",
        window_days: int = 30,
        min_occurrence: int = 1,
    ) -> list[CandidateRule]:
        """Run the full analysis pipeline: cluster → rank → produce candidates.

        Args:
            domain: Optional domain filter.
            window_days: Look-back window in days (approximate).
            min_occurrence: Minimum cluster size to become a candidate rule.

        Returns:
            List of :class:`CandidateRule` sorted by confidence descending.
        """
        clusters = self.cluster_edits(domain=domain, window_days=window_days)
        ranked = self.rank_candidates(clusters)
        return [
            c
            for c in ranked
            if c.occurrence_count >= min_occurrence
        ]

    # ── cluster ───────────────────────────────────────────────────────

    def cluster_edits(
        self, domain: str = "", window_days: int = 30
    ) -> dict[str, list[dict[str, Any]]]:
        """Group feedback records by normalized edit pattern.

        Args:
            domain: Optional domain filter.
            window_days: Look-back window in days (filters by ``created_at``).

        Returns:
            Dict mapping normalized operation key → list of operation records
            (each with ``feedback_id``, ``query_id``, ``op``, ``nl_input``,
            ``sql_generated``, ``sql_final``).
        """
        records = self.collector.get_recent(limit=500, domain=domain)

        from datetime import UTC, datetime, timedelta

        cutoff = datetime.now(UTC) - timedelta(days=window_days)

        clusters: dict[str, list[dict[str, Any]]] = {}

        for rec in records:
            # Time filter
            try:
                ts = datetime.fromisoformat(rec.get("created_at", ""))
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

            sql_gen = rec.get("sql_generated", "")
            sql_final = rec.get("sql_final", "")
            if not sql_final or sql_gen == sql_final:
                continue

            # ── Guard against spurious "edits" (test residue / data
            # inconsistency). These filters prevent PatternAnalyzer from
            # generating fake rule candidates from corrupted records. ──

            # Guard 1: require non-empty nl_input OR explicit diff ops.
            # Records with empty nl_input and diff_count=0 are auto-
            # generated (e.g. by record_query in learning.py) and never
            # represent real user edits. But if diff_count > 0 (the user
            # edited the SQL even though nl_input wasn't captured), we
            # still keep the record — the edit itself is the signal.
            nl_input = (rec.get("nl_input") or "").strip()
            diff_count = int(rec.get("diff_count", 0) or 0)
            if not nl_input and diff_count == 0:
                continue

            # Guard 2: require a meaningful edit (not just test residue).
            # This filters out garbage like "SELECT ...;tehstjsejr" or
            # "SELECT ...;111" appended to otherwise-valid SQL. We always
            # check this, even when diff_count > 0, because the frontend
            # always sets diff_count=1 for any sql_gen != sql_final —
            # including garbage appends.
            if not _is_meaningful_edit(sql_gen, sql_final):
                continue

            # Guard 3: require the edited SQL to be parseable.
            # This filters out garbage like "SELECT name FROM users;111"
            # or "SELECT name FROM users where uesr_id==1" (typo with
            # double-equals, which is invalid SQL).
            if not _is_valid_sql(sql_final):
                continue

            ops = _parse_diff_operations(sql_gen, sql_final)
            for op in ops:
                key = _normalize_operation(op)
                clusters.setdefault(key, []).append({
                    "feedback_id": rec.get("id", ""),
                    "query_id": rec.get("query_id", ""),
                    "op": op,
                    "nl_input": nl_input,
                    "sql_generated": sql_gen,
                    "sql_final": sql_final,
                    "domain_id": rec.get("domain_id", ""),
                })

        return clusters

    # ── rank & extract ────────────────────────────────────────────────

    def rank_candidates(
        self, clusters: dict[str, list[dict[str, Any]]]
    ) -> list[CandidateRule]:
        """Convert clusters into ranked :class:`CandidateRule` instances.

        Ranking score = log(occurrence_count) × consistency_bonus, where
        consistency_bonus = 1.0 if all operations in the cluster are the
        same type, 0.8 otherwise.

        Args:
            clusters: Output from :meth:`cluster_edits`.

        Returns:
            List of CandidateRule sorted by confidence descending.
        """
        import math
        from datetime import UTC, datetime

        candidates: list[CandidateRule] = []

        for key, items in clusters.items():
            if len(items) < 2:  # a single edit is noise, not a repeating pattern
                continue

            first = items[0]
            op_type = first["op"].get("keyword", "OTHER")
            table = first["op"].get("table", "")
            column = first["op"].get("column", "")
            add_remove = first["op"].get("type", "")
            raw_text = first["op"].get("raw_text", "")

            # Build human-readable description
            if add_remove == "add":
                desc = f"添加 {op_type}"
                if raw_text:
                    desc = f"用户添加了: {raw_text[:80]}"
                elif table and column:
                    desc += f" {table}.{column}"
                elif column:
                    desc += f" {column}"
            elif add_remove == "remove":
                desc = f"移除 {op_type}"
                if raw_text:
                    desc = f"用户移除了: {raw_text[:80]}"
                elif column:
                    desc += f" {column}"
            else:
                desc = f"修改 {op_type}"
                if raw_text:
                    desc = f"用户修改了: {raw_text[:80]}"

            # Check consistency
            types_in_cluster = {it["op"].get("keyword") for it in items}
            consistency = 1.0 if len(types_in_cluster) == 1 else 0.8

            # Score
            count = len(items)
            confidence = round(
                min(math.log(count + 1) / math.log(10) * consistency, 1.0), 2
            )

            # Build enforcement suggestion
            enforcement: dict[str, str] = {}
            if op_type in ("WHERE", "AND", "OR") and add_remove == "add":
                enforcement["require_condition"] = f"{table}.{column}" if table else (column or "")
            elif op_type == "AGGREGATE":
                enforcement["aggregation_note"] = desc

            # Build SQL template from the raw text fragment
            sql_template = raw_text if raw_text else (column or "")

            rule = CandidateRule(
                rule_id=f"cr_{hashlib.md5(key.encode()).hexdigest()[:8]}",
                domain_id=first.get("domain_id", ""),
                pattern_description=desc,
                suggested_rule_name=f"auto_{key.lower()}",
                suggested_pattern=re.escape(raw_text or column or ""),
                sql_template=sql_template,
                suggested_enforcement=enforcement,
                occurrence_count=count,
                supporting_query_ids=[
                    it["feedback_id"] for it in items if it.get("feedback_id")
                ],
                confidence=confidence,
                created_at=datetime.now(UTC).isoformat(),
            )
            candidates.append(rule)

        candidates.sort(key=lambda c: c.confidence, reverse=True)
        return candidates

    # ── review queue ──────────────────────────────────────────────────

    def get_review_queue(
        self,
        domain: str = "",
        status: str = "pending_review",
        limit: int = 50,
    ) -> list[CandidateRule]:
        """Get the current rule review queue.

        This is a convenience wrapper around :meth:`analyze` that also
        handles caching of previously seen candidates in a simple JSON
        sidecar file.

        Args:
            domain: Optional domain filter.
            status: Filter by status (``pending_review``, ``approved``, ``rejected``).
            limit: Maximum candidates to return.

        Returns:
            List of CandidateRule sorted by confidence descending.
        """
        # Always run fresh analysis; in production this would cache
        candidates = self.analyze(domain=domain)
        return [c for c in candidates if c.status == status][:limit]

    # ── persistence helpers ───────────────────────────────────────────

    def to_dicts(self, candidates: list[CandidateRule]) -> list[dict[str, Any]]:
        """Serialize candidates to JSON-safe dicts."""
        from dataclasses import asdict

        return [asdict(c) for c in candidates]

    @staticmethod
    def from_dicts(data: list[dict[str, Any]]) -> list[CandidateRule]:
        """Deserialize dicts back to CandidateRule instances."""
        return [
            CandidateRule(
                rule_id=d.get("rule_id", ""),
                domain_id=d.get("domain_id", ""),
                pattern_description=d.get("pattern_description", ""),
                suggested_rule_name=d.get("suggested_rule_name", ""),
                suggested_pattern=d.get("suggested_pattern", ""),
                sql_template=d.get("sql_template", ""),
                suggested_enforcement=d.get("suggested_enforcement", {}),
                occurrence_count=d.get("occurrence_count", 0),
                supporting_query_ids=d.get("supporting_query_ids", []),
                confidence=d.get("confidence", 0.0),
                status=d.get("status", "pending_review"),
                created_at=d.get("created_at", ""),
            )
            for d in data
        ]
