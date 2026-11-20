"""Approved review candidates become generation guidance.

The review queue already extracts candidate rules from user edits.  This module
is the missing consumer: it persists the review decision, maps approved rules
onto ``BusinessRule`` objects, and exposes the ones that match a query so the
prompt builder and the SQL validator can use them.

Scope limits, stated explicitly:

- Rules are guidance and non-blocking warnings, matching the existing business
  rule contract.  They never rewrite SQL on their own.
- Each tenant has its own local SQLite store. The default tenant retains the
  original file. The registry caches reads within the current process.
- A rule with an empty domain applies to every domain; a rule with a domain only
  applies to requests for that domain.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.auth.tenant import DEFAULT_TENANT_ID, get_current_tenant_id
from app.models.domain import BusinessRule

_ROOT = Path(__file__).resolve().parents[2]
_CACHE_TTL_SECONDS = 5.0


def _default_path() -> Path:
    configured = os.environ.get("DE_LEARNED_RULES", "").strip()
    if configured:
        return Path(configured)
    return _ROOT / "data" / "learned_rules.db"


def _enforce_list(value: Any) -> list[str]:
    """Normalise ``suggested_enforcement`` into ``key=value`` directives."""
    if isinstance(value, dict):
        return [f"{key}={item}" for key, item in value.items() if str(item).strip()]
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


@dataclass
class RuleRow:
    """A persisted review decision and the rule it describes."""

    rule_id: str
    status: str
    description: str = ""
    pattern: str = ""
    enforce: list[str] | None = None
    sql_template: str = ""
    domain: str = ""
    confidence: float = 0.0
    source_count: int = 0

    def to_business_rule(self) -> BusinessRule:
        return BusinessRule(
            id=self.rule_id,
            description=self.description or self.rule_id,
            pattern=self.pattern,
            enforce=list(self.enforce or []),
            sql_template=self.sql_template,
            domain_id=self.domain,
        )


class LearnedRuleStore:
    """SQLite persistence for review decisions."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path or _default_path())
        self._connections: dict[int, sqlite3.Connection] = {}

    # ── Connection ─────────────────────────────────────────────────

    def _connection(self) -> sqlite3.Connection:
        tenant_id = get_current_tenant_id()
        connection = self._connections.get(tenant_id)
        if connection is not None:
            return connection
        path = self._db_path
        if tenant_id != DEFAULT_TENANT_ID and str(path) != ":memory:":
            path = path.with_name(f"{path.stem}.tenant-{tenant_id}{path.suffix}")
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS learned_rules (
                    rule_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending_review',
                    description TEXT NOT NULL DEFAULT '',
                    pattern TEXT NOT NULL DEFAULT '',
                    enforce_json TEXT NOT NULL DEFAULT '[]',
                    sql_template TEXT NOT NULL DEFAULT '',
                    domain TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learned_rules_status
                    ON learned_rules (status, domain);
                """
            )
            connection.commit()
        except BaseException:
            connection.close()
            raise
        self._connections[tenant_id] = connection
        return connection

    # ── Writes ─────────────────────────────────────────────────────

    def upsert(self, rule: RuleRow) -> None:
        """Insert or update a rule, keeping supplied fields authoritative."""
        conn = self._connection()
        conn.execute(
            """
            INSERT INTO learned_rules
                (rule_id, status, description, pattern, enforce_json,
                 sql_template, domain, confidence, source_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rule_id) DO UPDATE SET
                status = excluded.status,
                description = excluded.description,
                pattern = excluded.pattern,
                enforce_json = excluded.enforce_json,
                sql_template = excluded.sql_template,
                domain = excluded.domain,
                confidence = excluded.confidence,
                source_count = excluded.source_count,
                updated_at = excluded.updated_at
            """,
            (
                rule.rule_id,
                rule.status,
                rule.description,
                rule.pattern,
                json.dumps(list(rule.enforce or []), ensure_ascii=False),
                rule.sql_template,
                rule.domain,
                rule.confidence,
                rule.source_count,
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ),
        )
        conn.commit()

    def set_status(self, rule_id: str, status: str) -> bool:
        """Update only the status of an existing rule."""
        conn = self._connection()
        cursor = conn.execute(
            "UPDATE learned_rules SET status = ?, updated_at = ? WHERE rule_id = ?",
            (status, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), rule_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    # ── Reads ──────────────────────────────────────────────────────

    def get(self, rule_id: str) -> RuleRow | None:
        row = self._connection().execute(
            "SELECT * FROM learned_rules WHERE rule_id = ?", (rule_id,)
        ).fetchone()
        return _row_to_rule(row) if row else None

    def list_rules(self, *, status: str = "", domain: str = "") -> list[RuleRow]:
        clauses: list[str] = []
        params: list[str] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if domain:
            clauses.append("(domain = ? OR domain = '')")
            params.append(domain)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection().execute(
            "SELECT * FROM learned_rules" + where + " ORDER BY confidence DESC, rule_id",
            params,
        ).fetchall()
        return [_row_to_rule(row) for row in rows]

    def approved(self, domain: str = "") -> list[RuleRow]:
        return self.list_rules(status="approved", domain=domain)

    def close(self) -> None:
        for connection in self._connections.values():
            connection.close()
        self._connections.clear()


def _row_to_rule(row: sqlite3.Row) -> RuleRow:
    try:
        enforce = json.loads(row["enforce_json"] or "[]")
    except (TypeError, ValueError):
        enforce = []
    return RuleRow(
        rule_id=row["rule_id"],
        status=row["status"],
        description=row["description"] or "",
        pattern=row["pattern"] or "",
        enforce=[str(item) for item in enforce] if isinstance(enforce, list) else [],
        sql_template=row["sql_template"] or "",
        domain=row["domain"] or "",
        confidence=float(row["confidence"] or 0.0),
        source_count=int(row["source_count"] or 0),
    )


class LearnedRuleRegistry:
    """Read-side facade: approved rules, cached briefly, matched per query."""

    def __init__(self, store: LearnedRuleStore | None = None) -> None:
        self._store = store or LearnedRuleStore()
        self._cache: dict[tuple[int, str], tuple[float, list[RuleRow], str]] = {}
        self._last_error: dict[int, str] = {}

    @property
    def store(self) -> LearnedRuleStore:
        return self._store

    @property
    def last_error(self) -> str:
        """Last read failure, or an empty string after a successful read."""
        return self._last_error.get(get_current_tenant_id(), "")

    def reset_cache(self) -> None:
        tenant_id = get_current_tenant_id()
        for key in [key for key in self._cache if key[0] == tenant_id]:
            del self._cache[key]

    def record_review(
        self,
        rule_id: str,
        status: str,
        info: dict[str, Any] | None = None,
    ) -> bool:
        """Persist a review decision from the API layer.

        Returns ``False`` when the store could not be written; callers must not
        fail the review endpoint because of an auxiliary store.
        """
        try:
            payload = info or {}
            existing = self._store.get(rule_id)

            def pick(field: str, default: Any = "") -> Any:
                value = payload.get(field)
                if value not in (None, ""):
                    return value
                return getattr(existing, field, default) if existing else default

            enforce = (
                _enforce_list(payload.get("suggested_enforcement"))
                or _enforce_list(payload.get("enforce"))
                or (list(existing.enforce or []) if existing else [])
            )
            row = RuleRow(
                rule_id=rule_id,
                status=status,
                description=str(pick("description")),
                pattern=str(pick("pattern")),
                enforce=enforce,
                sql_template=str(pick("sql_template")),
                domain=str(
                    payload.get("domain")
                    or payload.get("domain_id")
                    or (existing.domain if existing else "")
                ),
                confidence=float(pick("confidence", 0.0) or 0.0),
                source_count=int(
                    payload.get("source_count")
                    or payload.get("occurrence_count")
                    or (existing.source_count if existing else 0)
                ),
            )
            self._store.upsert(row)
            self.reset_cache()
            return True
        except Exception:
            return False

    def approved_rules(self, domain: str = "") -> list[RuleRow]:
        """Return approved rules for *domain* (plus domain-agnostic rules)."""
        tenant_id = get_current_tenant_id()
        key = (tenant_id, domain)
        cached = self._cache.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            self._last_error[tenant_id] = cached[2]
            return cached[1]
        error = ""
        try:
            rules = self._store.approved(domain)
        except Exception as exc:
            rules = []
            error = type(exc).__name__
        self._last_error[tenant_id] = error
        self._cache[key] = (now, rules, error)
        return rules

    def business_rules(self, domain: str = "") -> list[BusinessRule]:
        return [rule.to_business_rule() for rule in self.approved_rules(domain)]

    @staticmethod
    def match(query_text: str, rules: list[BusinessRule]) -> list[BusinessRule]:
        """Return rules whose pattern matches *query_text*.

        A rule without a pattern always matches.  Invalid regexes are treated as
        literal substrings so a malformed learned pattern cannot break a query.
        """
        matched: list[BusinessRule] = []
        for rule in rules:
            pattern = (rule.pattern or "").strip()
            if not pattern:
                matched.append(rule)
                continue
            try:
                if re.search(pattern, query_text, re.IGNORECASE):
                    matched.append(rule)
            except re.error:
                if pattern.lower() in query_text.lower():
                    matched.append(rule)
        return matched


learned_rule_registry = LearnedRuleRegistry()


def configure_learned_rules(db_path: str | Path | None = None) -> LearnedRuleRegistry:
    """Replace the process-wide registry (tests, release reloads, path changes)."""
    global learned_rule_registry
    learned_rule_registry = LearnedRuleRegistry(LearnedRuleStore(db_path))
    return learned_rule_registry
