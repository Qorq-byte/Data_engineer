r"""Glossary Manager — business term glossary with CRUD, matching, and resolution.

See implementation-plan §4.8 and progress-log §3.10 for the full specification.

The glossary manager provides a centralised registry of business terms
(:class:`~app.models.domain.GlossaryTerm`) with:

- **CRUD** operations scoped per-domain or global.
- **Matching** — fuzzy/substring search of glossary terms against NL query text.
- **Resolution** — map a business term to its SQL expression or schema objects
  (the ``resolve(term)`` interface required by ``SchemaRetriever``).

Usage::

    from app.knowledge.domain_manager import DomainManager
    from app.knowledge.glossary import GlossaryManager

    dm = DomainManager("app/config/domains")
    gm = GlossaryManager(domain_manager=dm)
    gm.load_from_domains()

    # Match
    hits = gm.match("查询订单金额", domain="ecommerce")
    # [("订单金额", GlosssaryTerm(...), 1.0), ...]

    # Resolve (for SchemaRetriever)
    names = gm.resolve("订单金额")  # → ["orders.amount"]

    # CRUD
    gm.add(GlossaryTerm(term="新客", ...), domain="ecommerce")
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from app.models.domain import GlossaryTerm, TermMapping

# ── GlossaryManager ────────────────────────────────────────────────────


class GlossaryManager:
    """Centralised business term glossary with CRUD, matching, and resolution.

    Terms are organised in two tiers:
    - **Global** (``domain=None``): available to all domains.
    - **Domain-scoped**: only active for a specific domain.

    Args:
        domain_manager: Optional ``DomainManager`` for loading terms from
                        domain YAML configs.
    """

    def __init__(self, domain_manager: Any = None) -> None:
        self._domain_manager = domain_manager
        # Global terms: term → GlossaryTerm
        self._global: dict[str, GlossaryTerm] = {}
        # Domain terms: domain → (term → GlossaryTerm)
        self._domain_terms: dict[str, dict[str, GlossaryTerm]] = {}

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def term_count(self) -> int:
        """Total number of registered terms across all scopes."""
        count = len(self._global)
        for terms in self._domain_terms.values():
            count += len(terms)
        return count

    @property
    def domain_count(self) -> int:
        """Number of domains with registered terms."""
        return len(self._domain_terms)

    # ── Loading ────────────────────────────────────────────────────────

    def load_from_domains(self) -> int:
        """Load all glossary terms from the attached ``DomainManager``.

        Returns:
            Number of terms loaded.
        """
        if self._domain_manager is None:
            return 0

        loaded = 0
        for name in self._domain_manager.list_all():
            domain = self._domain_manager.get(name)
            if domain is None:
                continue
            for term in domain.glossary:
                self.add(term, domain=name)
                loaded += 1
        return loaded

    # ── CRUD ───────────────────────────────────────────────────────────

    def add(self, term: GlossaryTerm, domain: str | None = None) -> None:
        """Add a glossary term.

        Args:
            term: The ``GlossaryTerm`` to add.
            domain: Optional domain scope.  ``None`` = global.
        """
        if domain:
            if domain not in self._domain_terms:
                self._domain_terms[domain] = {}
            self._domain_terms[domain][term.term] = term
        else:
            self._global[term.term] = term

    def get(self, term: str, domain: str | None = None) -> GlossaryTerm | None:
        """Look up a term by name.

        Searches domain scope first (when specified), then global.
        """
        if domain and domain in self._domain_terms:
            found = self._domain_terms[domain].get(term)
            if found is not None:
                return found
        return self._global.get(term)

    def update(
        self, term: str, updates: dict[str, Any], domain: str | None = None
    ) -> bool:
        """Update a term's fields in-place.

        Args:
            term: The term name to update.
            updates: Dict of field name → new value.  Supports any
                     ``GlossaryTerm`` field including ``mapping``.
            domain: Domain scope, or ``None`` for global.

        Returns:
            ``True`` if the term was found and updated.
        """
        existing = self._get_store(domain).get(term)
        if existing is None:
            # Also check global if domain was specified
            if domain:
                existing = self._global.get(term)
            if existing is None:
                return False

        for key, value in updates.items():
            if hasattr(existing, key):
                setattr(existing, key, value)
        return True

    def delete(self, term: str, domain: str | None = None) -> bool:
        """Delete a term.

        Args:
            term: The term name to delete.
            domain: Domain scope, or ``None`` for global.

        Returns:
            ``True`` if the term was found and deleted.
        """
        store = self._get_store(domain)
        if term in store:
            del store[term]
            return True
        if domain and term in self._global:
            del self._global[term]
            return True
        return False

    def list_all(self, domain: str | None = None) -> list[GlossaryTerm]:
        """List all terms, optionally filtered by *domain*.

        Args:
            domain: If provided, returns only that domain's terms.
                    If ``None``, returns global terms only.

        Returns:
            List of ``GlossaryTerm``, sorted by term name.
        """
        if domain:
            terms = self._domain_terms.get(domain, {})
            return sorted(terms.values(), key=lambda t: t.term)
        return sorted(self._global.values(), key=lambda t: t.term)

    def clear(self, domain: str | None = None) -> int:
        """Clear all terms, or only those in a specific *domain*.

        Returns:
            Number of terms removed.
        """
        if domain:
            count = len(self._domain_terms.get(domain, {}))
            self._domain_terms.pop(domain, None)
            return count
        else:
            count = self.term_count
            self._global.clear()
            self._domain_terms.clear()
            return count

    # ── Matching ───────────────────────────────────────────────────────

    def match(
        self,
        query_text: str,
        domain: str | None = None,
        threshold: float = 0.3,
    ) -> list[tuple[str, GlossaryTerm, float]]:
        """Match glossary terms against a natural-language *query_text*.

        Uses a combination of exact match, substring match, and fuzzy
        (SequenceMatcher) to find relevant terms.

        Args:
            query_text: The NL query to match against.
            domain: Optional domain filter.
            threshold: Minimum fuzzy similarity (0.0–1.0) for inclusion.

        Returns:
            List of ``(matched_term_name, GlossaryTerm, score)`` sorted
            by score descending.
        """
        candidates: dict[str, GlossaryTerm] = {}

        # Collect candidates: domain-scoped first, then global.
        # When no domain is specified, search ALL domains.
        if domain and domain in self._domain_terms:
            candidates.update(self._domain_terms[domain])
        elif domain is None:
            for d_terms in self._domain_terms.values():
                candidates.update(d_terms)
        candidates.update(self._global)

        text_lower = query_text.lower()
        results: list[tuple[str, GlossaryTerm, float]] = []

        for term_name, term in candidates.items():
            score = _term_match_score(term, text_lower)
            if score >= threshold:
                results.append((term_name, term, score))

        # Sort by score descending, then by term name for determinism
        results.sort(key=lambda x: (-x[2], x[0]))
        return results

    def match_best(
        self,
        query_text: str,
        domain: str | None = None,
        threshold: float = 0.5,
    ) -> tuple[str, GlossaryTerm, float] | None:
        """Return the single best matching term above *threshold*, or ``None``."""
        matches = self.match(query_text, domain=domain, threshold=threshold)
        return matches[0] if matches else None

    # ── Resolution ─────────────────────────────────────────────────────

    def resolve(
        self, term: str, domain: str | None = None
    ) -> list[str]:
        """Resolve a business term to a list of schema object names.

        This is the integration point for ``SchemaRetriever``, which calls
        this method via ``glossary.resolve(term)``.

        Resolution logic:
        1. Look up the term in the glossary.
        2. If the term has a ``TermMapping`` with a ``table``, return
           ``[table.column]`` (from the mapping expression).
        3. If the term has an ``expression`` but no explicit table, return
           the expression as a hint.
        4. Otherwise, return the term itself as a column name candidate.

        Args:
            term: Business term to resolve.
            domain: Optional domain scope.

        Returns:
            List of candidate schema object names (table or column).
        """
        entry = self.get(term, domain=domain)
        if entry is None:
            return _fallback_resolve(term)

        mapping = entry.mapping
        if mapping is None:
            return _fallback_resolve(term)

        results: list[str] = []

        # If the mapping has an explicit table, return table + column hints
        if mapping.table:
            # Try to extract column names from the expression
            cols = _extract_columns_from_expr(mapping.expression)
            for col in cols:
                results.append(f"{mapping.table}.{col}")
            if not cols:
                results.append(mapping.table)
            return results

        # No table — return expression-derived hints
        cols = _extract_columns_from_expr(mapping.expression)
        if cols:
            return cols
        return _fallback_resolve(term)

    def resolve_expression(
        self, term: str, domain: str | None = None
    ) -> TermMapping | None:
        """Return the ``TermMapping`` for a term, or ``None`` if not found."""
        entry = self.get(term, domain=domain)
        if entry is None:
            return None
        return entry.mapping

    # ── Internal ───────────────────────────────────────────────────────

    def _get_store(self, domain: str | None) -> dict[str, GlossaryTerm]:
        """Return the appropriate term store for a domain scope."""
        if domain:
            if domain not in self._domain_terms:
                self._domain_terms[domain] = {}
            return self._domain_terms[domain]
        return self._global


# ── Matching helpers ───────────────────────────────────────────────────


def _term_match_score(term: GlossaryTerm, text_lower: str) -> float:
    """Compute a match score (0.0–1.0) for a term against query text."""
    text_lower = text_lower.lower()
    term_lower = term.term.lower()
    term_en_lower = term.term_en.lower() if term.term_en else ""

    # Exact match in text
    if term_lower in text_lower:
        return 1.0
    if term_en_lower and term_en_lower in text_lower:
        return 0.95

    # Word-level match (term appears as a whole word)
    words = text_lower.replace(",", " ").replace("，", " ").split()
    if term_lower in words:
        return 0.9
    if term_en_lower and term_en_lower in words:
        return 0.85

    # Substring overlap (longer term = more specific)
    if len(term_lower) >= 3:
        if term_lower[:2] in text_lower:
            return 0.5
        if term_en_lower and len(term_en_lower) >= 3 and term_en_lower[:2] in text_lower:
            return 0.45

    # Fuzzy match via SequenceMatcher
    sm = SequenceMatcher(None, term_lower, text_lower)
    fuzzy_score = sm.ratio()
    if fuzzy_score >= 0.6:
        return round(fuzzy_score * 0.7, 3)  # discount fuzzy matches

    return 0.0


# ── Resolution helpers ────────────────────────────────────────────────


def _fallback_resolve(term: str) -> list[str]:
    """Fallback resolution: treat the term itself as a candidate name."""
    # Check for "table.column" pattern
    if "." in term:
        return [term]
    return [term]


def _extract_columns_from_expr(expression: str) -> list[str]:
    """Extract column names from a SQL expression.

    Looks for patterns like ``table.column`` or standalone identifiers
    that are likely column names.
    """
    import re

    columns: list[str] = []
    # Match table.column patterns
    for m in re.finditer(r'(\w+)\.(\w+)', expression):
        columns.append(m.group(2))

    # If no table.column pattern found, try to extract function args
    if not columns:
        # Match function arguments: SUM(col), AVG(col), COUNT(col), etc.
        func_arg_pattern = (
            r'(?:SUM|AVG|COUNT|MAX|MIN|COALESCE)\s*\(\s*(?:DISTINCT\s+)?(\w+(?:\.\w+)?)'
        )
        for m in re.finditer(func_arg_pattern, expression, re.IGNORECASE):
            arg = m.group(1)
            if "." in arg:
                columns.append(arg.split(".")[-1])
            else:
                columns.append(arg)

    return columns
