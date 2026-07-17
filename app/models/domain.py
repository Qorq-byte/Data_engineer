"""Domain configuration models — DomainConfig, GlossaryTerm, BusinessRule, etc.

See SPEC §4.2.2 for the full specification.
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class TermMapping:
    """Maps a business term to a SQL expression or condition."""

    expression: str
    type: Literal["derived_column", "filter_condition", "table_ref"] = "derived_column"
    table: str | None = None
    condition: str | None = None
    precision: int | None = None


@dataclass
class GlossaryTerm:
    """A single entry in the business term glossary."""

    term: str
    term_en: str = ""
    description: str = ""
    mapping: TermMapping | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class BusinessRule:
    """A business rule that constrains or guides SQL generation."""

    id: str
    description: str
    pattern: str = ""
    enforce: list[str] = field(default_factory=list)
    sql_template: str = ""
    domain_id: str = ""


@dataclass
class DomainConfig:
    """Configuration for a single domain (e.g., ecommerce, finance)."""

    name: str
    label: dict[str, str] = field(default_factory=dict)  # {"zh": "电商", "en": "E-commerce"}
    description: dict[str, str] = field(default_factory=dict)
    databases: list[dict] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    timezone: str = "UTC"
    currency: str = "USD"
    glossary: list[GlossaryTerm] = field(default_factory=list)
    rules: list[BusinessRule] = field(default_factory=list)


@dataclass
class DomainMatch:
    """Result of domain auto-detection for a given NL query."""

    domain: DomainConfig
    score: float  # 0.0 - 1.0
    matched_keywords: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)
