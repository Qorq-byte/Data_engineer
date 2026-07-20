r"""Domain Manager — YAML-based domain loading, switching, and auto-detection.

See implementation-plan §4.8.7 and progress-log §3.9 for the full specification.

The domain manager loads domain configuration from YAML files in a directory
(one file per domain), supports runtime switching, and implements a 3-layer
auto-detection strategy for matching NL queries to the right domain.

**3-layer detection**::

    L1: Keyword matching — domain keywords (zh + en) against query text
    L2: Term matching    — glossary terms against query text
    L3: Schema matching  — domain table names against query text or SchemaSnapshot

    Final score = 0.5 × L1 + 0.3 × L2 + 0.2 × L3
    [score ≥ 0.6 → auto-switch | < 0.6 → prompt user]

Usage::

    mgr = DomainManager("app/config/domains")
    mgr.load_all()
    mgr.set_active("ecommerce")

    # Auto-detect
    matches = mgr.detect("查询用户订单金额")
    best = mgr.detect_best("查询用户订单金额")  # → DomainMatch or None
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.models.domain import (
    BusinessRule,
    DomainConfig,
    DomainMatch,
    GlossaryTerm,
    TermMapping,
)

# ── DomainManager ──────────────────────────────────────────────────────


class DomainManager:
    """Manage domain configurations: load, switch, and auto-detect.

    Args:
        domain_dir: Path to a directory of ``<name>.yml`` domain config files.
        auto_load: If ``True`` (default), call :meth:`load_all` on construction.
    """

    def __init__(self, domain_dir: str, auto_load: bool = True) -> None:
        self._domain_dir = Path(domain_dir)
        self._domains: dict[str, DomainConfig] = {}
        self._active: str | None = None

        if auto_load and self._domain_dir.exists():
            self.load_all()

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def active_domain(self) -> DomainConfig | None:
        """Return the currently active domain config, or ``None``."""
        if self._active is None:
            return None
        return self._domains.get(self._active)

    @property
    def active_name(self) -> str | None:
        """Return the name of the currently active domain."""
        return self._active

    # ── Loading ────────────────────────────────────────────────────────

    def load_all(self) -> int:
        """Load (or reload) all domain YAML files from the domain directory.

        Returns:
            Number of domains loaded.
        """
        self._domains.clear()

        if not self._domain_dir.exists():
            return 0

        for path in sorted(self._domain_dir.glob("*.yml")):
            try:
                domain = self._load_file(path)
                self._domains[domain.name] = domain
            except Exception:
                # Skip malformed files with a warning
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to load domain config: %s", path, exc_info=True,
                )

        return len(self._domains)

    def reload(self) -> int:
        """Alias for :meth:`load_all` — hot-reload all domain configs."""
        return self.load_all()

    def _load_file(self, path: Path) -> DomainConfig:
        """Parse a single domain YAML file into a DomainConfig."""
        with open(path, encoding="utf-8") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh) or {}

        # Glossary
        glossary: list[GlossaryTerm] = []
        for g in raw.get("glossary", []) or []:
            mapping_raw = g.get("mapping")
            mapping = None
            if mapping_raw:
                mapping = TermMapping(
                    expression=mapping_raw.get("expression", ""),
                    type=mapping_raw.get("type", "derived_column"),
                    table=mapping_raw.get("table"),
                    condition=mapping_raw.get("condition"),
                    precision=mapping_raw.get("precision"),
                )
            glossary.append(GlossaryTerm(
                term=g.get("term", ""),
                term_en=g.get("term_en", ""),
                description=g.get("description", ""),
                mapping=mapping,
                tags=g.get("tags", []),
            ))

        # Rules
        rules: list[BusinessRule] = []
        for r in raw.get("rules", []) or []:
            rules.append(BusinessRule(
                id=r.get("id", ""),
                description=r.get("description", ""),
                pattern=r.get("pattern", ""),
                enforce=r.get("enforce", []),
                sql_template=r.get("sql_template", ""),
                domain_id=raw.get("name", ""),
            ))

        return DomainConfig(
            name=raw.get("name", ""),
            label=raw.get("label", {}),
            description=raw.get("description", {}),
            databases=raw.get("databases", []),
            keywords=raw.get("keywords", []),
            timezone=raw.get("timezone", "UTC"),
            currency=raw.get("currency", "USD"),
            glossary=glossary,
            rules=rules,
        )

    # ── Access ─────────────────────────────────────────────────────────

    def get(self, name: str) -> DomainConfig | None:
        """Return a domain config by name, or ``None``."""
        return self._domains.get(name)

    def list_all(self) -> list[str]:
        """Return a sorted list of all registered domain names."""
        return sorted(self._domains.keys())

    def __len__(self) -> int:
        return len(self._domains)

    def __contains__(self, name: str) -> bool:
        return name in self._domains

    # ── Activation ─────────────────────────────────────────────────────

    def set_active(self, name: str) -> None:
        """Set the active domain.

        Raises:
            KeyError: If *name* is not a loaded domain.
        """
        if name not in self._domains:
            available = ", ".join(sorted(self._domains.keys()))
            raise KeyError(
                f"Domain '{name}' not found. Available: [{available}]"
            )
        self._active = name

    def clear_active(self) -> None:
        """Clear the active domain selection."""
        self._active = None

    # ── Detection ──────────────────────────────────────────────────────

    def detect(
        self,
        query_text: str,
        schema: Any | None = None,
    ) -> list[DomainMatch]:
        """Run 3-layer auto-detection and return all domain matches.

        Args:
            query_text: Natural-language query text.
            schema: Optional ``SchemaSnapshot`` (or any object with a
                    ``tables`` dict).  Used for L3 schema matching.

        Returns:
            List of ``DomainMatch`` sorted by score descending.
        """
        if not self._domains:
            return []

        text_lower = query_text.lower()

        matches: list[DomainMatch] = []

        for domain in self._domains.values():
            matched_keywords: list[str] = []
            matched_terms: list[str] = []

            # L1: Keyword matching (weight 0.5)
            kw_hits = 0
            for kw in domain.keywords:
                if kw.lower() in text_lower:
                    kw_hits += 1
                    matched_keywords.append(kw)
            kw_score = kw_hits / max(len(domain.keywords), 1)

            # L2: Term matching (weight 0.3)
            term_hits = 0
            for term in domain.glossary:
                if term.term.lower() in text_lower or (
                    term.term_en and term.term_en.lower() in text_lower
                ):
                    term_hits += 1
                    matched_terms.append(term.term)
            term_score = term_hits / max(len(domain.glossary), 1)

            # L3: Schema matching (weight 0.2)
            schema_score = 0.0
            if schema is not None and hasattr(schema, "tables"):
                schema_tables = schema.tables
                domain_tables: set[str] = set()
                for db in domain.databases:
                    for t in db.get("tables", []) or []:
                        domain_tables.add(t.lower())

                if domain_tables:
                    # Count how many domain tables appear in the schema
                    matched = sum(
                        1 for t in domain_tables if t in schema_tables
                    )
                    # Also check if table names appear in query text
                    text_matches = sum(
                        1 for t in domain_tables if t.lower() in text_lower
                    )
                    schema_score = (matched + text_matches) / max(
                        2 * len(domain_tables), 1
                    )

            # Weighted final score
            score = 0.5 * kw_score + 0.3 * term_score + 0.2 * schema_score

            matches.append(DomainMatch(
                domain=domain,
                score=round(min(score, 1.0), 4),
                matched_keywords=matched_keywords,
                matched_terms=matched_terms,
            ))

        # Sort by score descending, then by domain name for determinism
        matches.sort(key=lambda m: (-m.score, m.domain.name))
        return matches

    def detect_best(
        self,
        query_text: str,
        schema: Any | None = None,
        threshold: float = 0.6,
    ) -> DomainMatch | None:
        """Return the best domain match above *threshold*, or ``None``.

        Args:
            query_text: Natural-language query text.
            schema: Optional ``SchemaSnapshot``.
            threshold: Minimum score to auto-select (default 0.6).

        Returns:
            The top ``DomainMatch`` if its score ≥ *threshold*; ``None`` otherwise.
        """
        matches = self.detect(query_text, schema=schema)
        if not matches:
            return None
        best = matches[0]
        return best if best.score >= threshold else None
