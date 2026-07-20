r"""Rule Engine — business rule matching and enforcement for NL→SQL generation.

See implementation-plan §4.8 and progress-log §3.11 for the full specification.

The rule engine manages :class:`~app.models.domain.BusinessRule` instances and
provides pattern-based matching against natural-language queries.  Matched rules
produce *enforcement directives* (e.g. ``precision=2``, ``timezone=Asia/Shanghai``,
``require_time_range=true``) that downstream nodes (GenerateSQLNode, ValidateSQLNode)
consume to constrain SQL generation.

**Architecture**::

    NL query → RuleEngine.match() → [RuleMatch, ...]
                │
                ├─ get_enforcements() → {"precision": "2", "timezone": "Asia/Shanghai"}
                └─ apply_sql_template() → filled SQL fragment

Usage::

    from app.knowledge.domain_manager import DomainManager
    from app.knowledge.rule_engine import RuleEngine

    dm = DomainManager("app/config/domains")
    engine = RuleEngine(domain_manager=dm)
    engine.load_from_domains()

    # Match rules against an NL query
    matches = engine.match("查询订单金额", domain="ecommerce")
    # [RuleMatch(rule=BusinessRule(...), score=1.0)]

    # Get enforcement directives for downstream use
    directives = engine.get_enforcements("查询订单金额", domain="ecommerce")
    # → {"precision": "2", "timezone": "Asia/Shanghai"}
"""

from __future__ import annotations

import re
from typing import Any

from app.models.domain import BusinessRule

# ── RuleMatch ────────────────────────────────────────────────────────────


class RuleMatch:
    """A matched business rule with its relevance score.

    Attributes:
        rule: The matched ``BusinessRule``.
        score: Match confidence (0.0–1.0).  1.0 = full pattern match.
        matched_groups: Regex capture groups from the pattern match, if any.
    """

    __slots__ = ("rule", "score", "matched_groups")

    def __init__(
        self,
        rule: BusinessRule,
        score: float,
        matched_groups: tuple[str, ...] = (),
    ) -> None:
        self.rule = rule
        self.score = score
        self.matched_groups = matched_groups

    def __repr__(self) -> str:
        return (
            f"RuleMatch(rule_id={self.rule.id!r}, score={self.score:.3f}, "
            f"matched_groups={self.matched_groups!r})"
        )


# ── RuleEngine ───────────────────────────────────────────────────────────


class RuleEngine:
    """Business rule engine — pattern matching + enforcement directive extraction.

    Rules are organised in two tiers:
    - **Global** (``domain=None``): available to all domains.
    - **Domain-scoped**: only active for a specific domain.

    Args:
        domain_manager: Optional ``DomainManager`` for loading rules from
                        domain YAML configs.
    """

    def __init__(self, domain_manager: Any = None) -> None:
        self._domain_manager = domain_manager
        # Global rules: id → BusinessRule
        self._global: dict[str, BusinessRule] = {}
        # Domain rules: domain → (id → BusinessRule)
        self._domain_rules: dict[str, dict[str, BusinessRule]] = {}

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def rule_count(self) -> int:
        """Total number of registered rules across all scopes."""
        count = len(self._global)
        for rules in self._domain_rules.values():
            count += len(rules)
        return count

    @property
    def domain_count(self) -> int:
        """Number of domains with registered rules."""
        return len(self._domain_rules)

    # ── Loading ────────────────────────────────────────────────────────

    def load_from_domains(self) -> int:
        """Load all business rules from the attached ``DomainManager``.

        Returns:
            Number of rules loaded.
        """
        if self._domain_manager is None:
            return 0

        loaded = 0
        for name in self._domain_manager.list_all():
            domain = self._domain_manager.get(name)
            if domain is None:
                continue
            for rule in domain.rules:
                self.add(rule, domain=name)
                loaded += 1
        return loaded

    # ── CRUD ───────────────────────────────────────────────────────────

    def add(self, rule: BusinessRule, domain: str | None = None) -> None:
        """Register a business rule.

        Args:
            rule: The ``BusinessRule`` to add.
            domain: Optional domain scope.  ``None`` = global.
        """
        if domain:
            if domain not in self._domain_rules:
                self._domain_rules[domain] = {}
            self._domain_rules[domain][rule.id] = rule
        else:
            self._global[rule.id] = rule

    def get(self, rule_id: str, domain: str | None = None) -> BusinessRule | None:
        """Look up a rule by ID.

        Searches domain scope first (when specified), then global.
        """
        if domain and domain in self._domain_rules:
            found = self._domain_rules[domain].get(rule_id)
            if found is not None:
                return found
        return self._global.get(rule_id)

    def update(
        self, rule_id: str, updates: dict[str, Any], domain: str | None = None
    ) -> bool:
        """Update a rule's fields in-place.

        Args:
            rule_id: The rule ID to update.
            updates: Dict of field name → new value.
            domain: Domain scope, or ``None`` for global.

        Returns:
            ``True`` if the rule was found and updated.
        """
        existing = self._get_store(domain).get(rule_id)
        if existing is None and domain:
            existing = self._global.get(rule_id)
        if existing is None:
            return False

        for key, value in updates.items():
            if hasattr(existing, key):
                setattr(existing, key, value)
        return True

    def delete(self, rule_id: str, domain: str | None = None) -> bool:
        """Delete a rule by ID.

        Args:
            rule_id: The rule ID to delete.
            domain: Domain scope, or ``None`` for global.

        Returns:
            ``True`` if the rule was found and deleted.
        """
        store = self._get_store(domain)
        if rule_id in store:
            del store[rule_id]
            return True
        if domain and rule_id in self._global:
            del self._global[rule_id]
            return True
        return False

    def list_all(self, domain: str | None = None) -> list[BusinessRule]:
        """List all rules, optionally filtered by *domain*.

        Args:
            domain: If provided, returns only that domain's rules.
                    If ``None``, returns global rules only.

        Returns:
            List of ``BusinessRule``, sorted by ID.
        """
        if domain:
            rules = self._domain_rules.get(domain, {})
            return sorted(rules.values(), key=lambda r: r.id)
        return sorted(self._global.values(), key=lambda r: r.id)

    def clear(self, domain: str | None = None) -> int:
        """Clear all rules, or only those in a specific *domain*.

        Returns:
            Number of rules removed.
        """
        if domain:
            count = len(self._domain_rules.get(domain, {}))
            self._domain_rules.pop(domain, None)
            return count
        else:
            count = self.rule_count
            self._global.clear()
            self._domain_rules.clear()
            return count

    # ── Matching ───────────────────────────────────────────────────────

    def match(
        self,
        query_text: str,
        domain: str | None = None,
        threshold: float = 0.0,
    ) -> list[RuleMatch]:
        """Match business rules against a natural-language *query_text*.

        Each rule's ``pattern`` field is compiled as a regex (case-insensitive).
        The match score reflects how much of the query the pattern covers.

        Args:
            query_text: The NL query to match against.
            domain: Optional domain filter.
            threshold: Minimum score (0.0–1.0) for inclusion.

        Returns:
            List of ``RuleMatch`` sorted by score descending.
        """
        candidates: dict[str, BusinessRule] = {}

        # Collect candidates: domain-scoped first, then global
        if domain and domain in self._domain_rules:
            candidates.update(self._domain_rules[domain])
        elif domain is None:
            for d_rules in self._domain_rules.values():
                candidates.update(d_rules)
        candidates.update(self._global)

        results: list[RuleMatch] = []

        for rule in candidates.values():
            if not rule.pattern:
                continue
            score, groups = _match_pattern(rule.pattern, query_text)
            if score > 0.0 and score >= threshold:
                results.append(RuleMatch(rule=rule, score=score, matched_groups=groups))

        # Sort by score descending, then by rule ID for determinism
        results.sort(key=lambda m: (-m.score, m.rule.id))
        return results

    def match_best(
        self,
        query_text: str,
        domain: str | None = None,
        threshold: float = 0.0,
    ) -> RuleMatch | None:
        """Return the single best matching rule above *threshold*, or ``None``."""
        matches = self.match(query_text, domain=domain, threshold=threshold)
        return matches[0] if matches else None

    # ── Enforcement ────────────────────────────────────────────────────

    def get_enforcements(
        self,
        query_text: str,
        domain: str | None = None,
        threshold: float = 0.0,
    ) -> dict[str, str]:
        """Extract enforcement directives from all matching rules.

        Each rule's ``enforce`` list contains ``key=value`` strings (e.g.
        ``"precision=2"``, ``"timezone=Asia/Shanghai"``).  This method
        parses them into a flat ``{key: value}`` dict.

        When multiple rules set the same key, the rule with the **higher
        match score** wins.

        Args:
            query_text: The NL query to match against.
            domain: Optional domain filter.
            threshold: Minimum score for rule inclusion.

        Returns:
            Flat dict of enforcement key → value.
        """
        matches = self.match(query_text, domain=domain, threshold=threshold)
        enforcements: dict[str, str] = {}

        for rm in matches:
            for directive in rm.rule.enforce:
                if "=" in directive:
                    key, _, value = directive.partition("=")
                    # Higher-score rule wins; first match for a given key
                    # already has the highest score (matches are sorted).
                    if key not in enforcements:
                        enforcements[key] = value

        return enforcements

    # ── SQL Template ───────────────────────────────────────────────────

    def apply_sql_template(
        self,
        rule_id: str,
        params: dict[str, str] | None = None,
        domain: str | None = None,
    ) -> str | None:
        """Substitute *params* into a rule's ``sql_template``.

        Uses simple ``{key}`` placeholder substitution (Python ``str.format``).

        Args:
            rule_id: The rule whose ``sql_template`` to use.
            params: Key-value pairs for placeholder substitution.
            domain: Optional domain scope.

        Returns:
            The filled SQL fragment, or ``None`` if the rule has no template.
        """
        rule = self.get(rule_id, domain=domain)
        if rule is None or not rule.sql_template:
            return None

        if params:
            return rule.sql_template.format(**params)
        return rule.sql_template

    # ── Internal ───────────────────────────────────────────────────────

    def _get_store(self, domain: str | None) -> dict[str, BusinessRule]:
        """Return the appropriate rule store for a domain scope."""
        if domain:
            if domain not in self._domain_rules:
                self._domain_rules[domain] = {}
            return self._domain_rules[domain]
        return self._global


# ── Pattern matching helper ──────────────────────────────────────────────


def _match_pattern(pattern: str, text: str) -> tuple[float, tuple[str, ...]]:
    """Match a regex *pattern* against *text* (case-insensitive).

    Returns:
        A ``(score, groups)`` tuple.  Score is the fraction of the text
        covered by the match (0.0–1.0), and groups are any capture groups.
    """
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return 0.0, ()

    matches = list(compiled.finditer(text))
    if not matches:
        return 0.0, ()

    # Score = coverage ratio of all matches (union) vs total text length
    covered: set[int] = set()
    groups: list[str] = []

    for m in matches:
        for i in range(m.start(), m.end()):
            if i < len(text):
                covered.add(i)
        groups.extend(g for g in m.groups() if g is not None)

    text_len = max(len(text), 1)
    score = len(covered) / text_len

    return round(min(score, 1.0), 4), tuple(groups)
