"""Query-related models — SQR, SQLCandidate, ValidationReport, etc.

See SPEC §4.1.3 (SQR) and §4.5.1 (ValidationReport) for the full specification.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal


class IntentType(StrEnum):
    SELECT = "SELECT"
    AGGREGATE = "AGGREGATE"
    JOIN = "JOIN"
    COMPARISON = "COMPARISON"
    TIME_SERIES = "TIME_SERIES"
    FUNNEL = "FUNNEL"
    UNKNOWN = "UNKNOWN"


Language = Literal["zh", "en", "mixed"]


@dataclass
class Entity:
    """A named entity extracted from an NL query."""

    name: str
    type: str  # "table", "column", "term", "value", "time"
    normalized: str
    confidence: float = 1.0
    start_pos: int = 0
    end_pos: int = 0


@dataclass
class TimeRange:
    """A standardized time range from parsed time expressions."""

    start: datetime | None = None
    end: datetime | None = None
    unit: str | None = None  # "day", "week", "month", "quarter", "year"
    precision: str = "day"
    raw_expression: str = ""


@dataclass
class Condition:
    """A single WHERE condition extracted from NL."""

    column: str
    operator: str  # "=", ">", "<", "IN", "LIKE", "BETWEEN", etc.
    value: Any
    logic: Literal["AND", "OR"] = "AND"


@dataclass
class OrderSpec:
    """ORDER BY specification."""

    column: str
    direction: Literal["ASC", "DESC"] = "ASC"


@dataclass
class Ambiguity:
    """A detected ambiguity point in the NL query."""

    aspect: str  # "attribute", "aggregation", "range", "time_reference"
    description: str
    options: list[str] = field(default_factory=list)
    default: str | None = None


@dataclass
class SQR:
    """Structured Query Representation — the bridge between NL and SQL."""

    raw_text: str
    language: Language = "en"
    intent: IntentType = IntentType.UNKNOWN
    entities: list[Entity] = field(default_factory=list)
    time_range: TimeRange | None = None
    target_tables: list[str] = field(default_factory=list)
    target_columns: list[str] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    order_by: list[OrderSpec] = field(default_factory=list)
    limit: int | None = None
    ambiguities: list[Ambiguity] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class SQLCandidate:
    """A single generated SQL candidate with metadata."""

    id: str
    sql_text: str
    confidence: float = 0.0
    generation_mode: str = "primary"  # "primary", "alt_1", "alt_2", "self_heal"
    reasoning: str = ""
    dialect: str = "ansi"
    model: str = ""
    latency_ms: float = 0.0
    database_name: str = ""           # which database this SQL targets (for multi-DB)
    validation: dict | None = None   # per-candidate validation result
    execution: dict | None = None    # per-candidate execution result


@dataclass
class PlanAnalysis:
    """Analysis of a SQL execution plan."""

    scan_types: list[str] = field(default_factory=list)
    estimated_rows: int = 0
    join_types: list[str] = field(default_factory=list)
    has_full_scan: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """Result of multi-stage SQL validation."""

    passed: bool = False
    syntax_ok: bool = False
    syntax_errors: list[str] = field(default_factory=list)
    schema_valid: bool = False
    schema_errors: list[str] = field(default_factory=list)
    type_valid: bool = False
    type_errors: list[str] = field(default_factory=list)
    plan_analysis: PlanAnalysis | None = None
    business_valid: bool = False
    business_warnings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class QueryPair:
    """A known NL→SQL pair used as few-shot example in prompts."""

    nl_text: str
    sql_text: str
    domain: str = ""
    similarity: float = 0.0  # semantic similarity to current query


@dataclass
class QueryResult:
    """Result of executing a SQL query."""

    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    truncated: bool = False
    error: str | None = None
