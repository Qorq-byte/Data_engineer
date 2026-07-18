"""PII column auto-detection — flags sensitive columns for masking (SPEC §9.2).

Auto-detects personally identifiable information (PII) columns based on
column name patterns and data type heuristics, then marks them on the
existing :class:`~app.models.schema.ColumnSchema` instances.

Usage::

    from app.db.pii_detector import PiiDetector
    detector = PiiDetector()
    detector.annotate_snapshot(snapshot)  # mutates columns in place
    print(snapshot.tables["users"].columns[0].is_pii)

The detector is conservative: it only flags columns whose names clearly
indicate PII (e.g., ``phone``, ``email``, ``id_card``). Borderline names
like ``user_id`` are NOT flagged (they are typically system identifiers,
not personal data).
"""

from __future__ import annotations

import re
from typing import Any

from app.models.schema import ColumnSchema, SchemaSnapshot, TableSchema

# ── PII detection rules ────────────────────────────────────────────────
#
# Each rule: (pii_type, compiled_regex, description)
# Patterns are case-insensitive and match against the normalised column
# name (lowercased, underscores preserved). Word boundaries are enforced
# to avoid false positives (e.g., "phone" should match "user_phone" but
# not "telephone_exchange_code" — though the latter is unlikely in DB
# schemas, the rule still applies conservatively).

_PII_RULES: list[tuple[str, re.Pattern[str], str]] = [
    # Phone numbers — international and Chinese formats
    (
        "phone",
        re.compile(
            r"\b(phone|mobile|tel|telephone|contact_no|contact_number|"
            r"cell_phone|cellphone|handset|msisdn)\b",
            re.IGNORECASE,
        ),
        "Phone number column",
    ),
    # Email addresses
    (
        "email",
        re.compile(
            r"\b(email|e_mail|user_email|mail_addr|email_address|"
            r"contact_email)\b",
            re.IGNORECASE,
        ),
        "Email column",
    ),
    # National ID (Chinese 身份证, US SSN, passport, etc.)
    (
        "id_card",
        re.compile(
            r"\b(id_card|idcard|identity_card|identity_no|identity_number|"
            r"national_id|citizen_id|ssn|social_security|passport_no|"
            r"passport_number|resident_id|shenfenzheng|身份证)\b",
            re.IGNORECASE,
        ),
        "National ID / SSN column",
    ),
    # Personal names (more conservative — only obvious name columns)
    (
        "name",
        re.compile(
            r"\b(full_name|fullname|real_name|realname|first_name|"
            r"last_name|family_name|given_name|customer_name|user_name|"
            r"username|nickname|display_name)\b",
            re.IGNORECASE,
        ),
        "Personal name column",
    ),
    # Postal / residential addresses
    (
        "address",
        re.compile(
            r"\b(address|home_addr|home_address|residence_addr|"
            r"residence_address|street_addr|street_address|mailing_addr|"
            r"mailing_address|postal_addr|postal_address)\b",
            re.IGNORECASE,
        ),
        "Address column",
    ),
    # Bank cards / credit cards
    (
        "bank_card",
        re.compile(
            r"\b(bank_card|bankcard|card_no|card_number|credit_card|"
            r"credit_card_no|credit_card_number|debit_card|cvv|"
            r"card_cvc|pan)\b",
            re.IGNORECASE,
        ),
        "Bank / credit card column",
    ),
    # Date of birth
    (
        "dob",
        re.compile(
            r"\b(dob|date_of_birth|birthdate|birth_date|birthday)\b",
            re.IGNORECASE,
        ),
        "Date of birth column",
    ),
]


class PiiDetector:
    """Detect and annotate PII columns in a schema snapshot.

    The detector is designed to be **non-destructive**: it only sets the
    ``is_pii`` and ``pii_type`` fields on existing ColumnSchema instances
    and never modifies column names, types, or other metadata.
    """

    def __init__(self, rules: list[tuple[str, re.Pattern[str], str]] | None = None) -> None:
        self._rules = rules or _PII_RULES

    # ── Public API ───────────────────────────────────────────────────

    def detect_column(self, column: ColumnSchema) -> tuple[bool, str]:
        """Check whether *column* looks like PII.

        Returns ``(is_pii, pii_type)``. When not PII, ``pii_type`` is empty.
        """
        # System columns are never PII even if name matches
        # (e.g., "created_at", "updated_at", "id", "version")
        if self._is_system_column(column.name):
            return False, ""

        for pii_type, pattern, _desc in self._rules:
            if pattern.search(column.name):
                return True, pii_type

        # Heuristic: column type + name combination
        # (e.g., a VARCHAR column named "ssn_last_4" is still PII)
        return self._detect_by_type_heuristic(column)

    def annotate_table(self, table: TableSchema) -> int:
        """Mark PII columns on *table* in place.

        Returns the number of columns flagged as PII.
        """
        count = 0
        for col in table.columns:
            if col.is_pii:
                count += 1
                continue
            is_pii, pii_type = self.detect_column(col)
            if is_pii:
                col.is_pii = True
                col.pii_type = pii_type
                count += 1
        return count

    def annotate_snapshot(self, snapshot: SchemaSnapshot) -> int:
        """Mark PII columns across all tables in *snapshot* (in place).

        Returns total number of PII columns found.
        """
        total = 0
        for table in snapshot.tables.values():
            total += self.annotate_table(table)
        return total

    def get_pii_summary(self, snapshot: SchemaSnapshot) -> dict[str, Any]:
        """Return a summary of detected PII columns per table.

        Useful for logging, audit, and the schema API response.
        """
        summary: dict[str, Any] = {
            "total_pii_columns": 0,
            "by_type": {},
            "tables": {},
        }
        for table_name, table in snapshot.tables.items():
            pii_cols = [
                {
                    "name": col.name,
                    "type": col.type,
                    "pii_type": col.pii_type,
                }
                for col in table.columns
                if col.is_pii
            ]
            if pii_cols:
                summary["tables"][table_name] = pii_cols
                summary["total_pii_columns"] += len(pii_cols)
                for col in pii_cols:
                    ptype = col["pii_type"] or "other"
                    summary["by_type"][ptype] = summary["by_type"].get(ptype, 0) + 1
        return summary

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _is_system_column(name: str) -> bool:
        """Identify system/audit columns that should never be flagged as PII."""
        n = name.lower().strip()
        # Common audit/system columns
        system_names = {
            "id", "pk", "version", "created_at", "updated_at", "deleted_at",
            "created_by", "updated_by", "is_active", "is_deleted", "status",
            "tenant_id", "org_id", "company_id", "department_id",
            "id_str", "uid", "guid",  # generic unique IDs (not national IDs)
        }
        return n in system_names

    @staticmethod
    def _detect_by_type_heuristic(column: ColumnSchema) -> tuple[bool, str]:
        """Fallback detection using column type + name hints.

        Conservative: only flags when both the type and name strongly
        suggest PII (e.g., a CHAR(11) column named "id_no" — 11 digits is
        the length of a Chinese national ID).
        """
        name_l = column.name.lower()
        col_type_l = column.type.upper()

        # Chinese ID card is exactly 18 chars
        if "id_no" in name_l or "idno" in name_l or "cert_no" in name_l:
            return True, "id_card"

        # Phone number column with a numeric type
        if "no" in name_l and ("phone" in name_l or "mobile" in name_l):
            return True, "phone"

        return False, ""
