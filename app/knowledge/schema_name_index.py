"""Precomputed name lookups for the keyword schema linker.

``SchemaLinkingNode`` answered every candidate by walking the whole snapshot,
so one query cost O(candidates x tables x columns): at 10k tables a single
request re-read 120k column names dozens of times.  The matching rules
themselves are cheap; the repeated normalisation and rescanning are not.

This module builds the lookup structures once per snapshot and answers the same
rules from dictionaries plus a couple of C-level ``str.find`` scans.  Confidence
values, thresholds and result ordering are unchanged: every rule below mirrors
the ``if/elif`` chain it replaced, and because those confidences strictly
decrease the "first rule that matches" behaviour is reproduced by keeping the
highest confidence per name.  ``tests/test_knowledge/test_schema_name_index.py``
keeps a copy of the original loops and compares both over randomised schemas.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Iterator, Mapping, Sequence

from app.models.schema import SchemaSnapshot

_BLOB_SEPARATOR = "\x00"
# Substring enumeration is quadratic in the needle length; refuse to explode on
# an absurd candidate and fall back to a linear scan instead.
_MAX_SUBSTRING_NEEDLE = 128


def _blob(names: Sequence[str]) -> tuple[str, list[int]]:
    """Join *names* with a separator and remember where each one starts."""
    starts: list[int] = []
    offset = 0
    for name in names:
        starts.append(offset)
        offset += len(name) + 1
    return _BLOB_SEPARATOR.join(names), starts


def _substrings(text: str, min_length: int = 1) -> Iterator[str]:
    """Yield every substring of *text* at least *min_length* long."""
    length = len(text)
    for start in range(length):
        for end in range(start + min_length, length + 1):
            yield text[start:end]


def _positions_name_index(blob: str, starts: Sequence[int], needle: str) -> list[int]:
    """Return the indexes of the names in *blob* that contain *needle*."""
    if not needle:
        return list(range(len(starts)))
    if _BLOB_SEPARATOR in needle:
        return []
    found: list[int] = []
    seen: set[int] = set()
    cursor = 0
    while True:
        position = blob.find(needle, cursor)
        if position < 0:
            return found
        cursor = position + 1
        index = bisect_right(starts, position) - 1
        if index >= 0 and index not in seen:
            seen.add(index)
            found.append(index)


class SchemaNameIndex:
    """Name lookups for one :class:`SchemaSnapshot`.

    Build cost is a single pass over the snapshot (one ``lower()`` per name);
    everything afterwards is dictionary work, so building it once per request
    is much cheaper than rescanning per candidate.
    """

    def __init__(self, schema: SchemaSnapshot) -> None:
        self._table_names: list[str] = []
        self._table_lower: list[str] = []
        self._table_order: dict[str, int] = {}
        self._table_lower_index: dict[str, list[int]] = {}
        self._table_comments: list[tuple[int, str]] = []
        self._part_tables: dict[str, list[int]] = {}

        self._column_exact: dict[str, list[tuple[int, str, str]]] = {}
        self._column_lower: dict[str, list[tuple[int, str, str]]] = {}
        self._column_comments: list[tuple[int, str, str, str]] = []
        self._column_names: list[str] = []
        self._column_name_order: dict[str, int] = {}
        self._column_order: list[tuple[int, str, str]] = []

        column_order = 0
        for table_name, table in schema.tables.items():
            table_order = len(self._table_names)
            self._table_names.append(table_name)
            lower_name = table_name.lower()
            self._table_lower.append(lower_name)
            self._table_order[table_name] = table_order
            self._table_lower_index.setdefault(lower_name, []).append(table_order)
            if table.comment:
                self._table_comments.append((table_order, table.comment.lower()))

            for part in table_name.replace("-", "_").split("_"):
                part_lower = part.lower().strip()
                if len(part_lower) < 3:
                    continue
                bucket = self._part_tables.setdefault(part_lower, [])
                if not bucket or bucket[-1] != table_order:
                    bucket.append(table_order)

            for column in table.columns:
                column_lower = column.name.lower()
                entry = (column_order, table_name, column.name)
                self._column_order.append(entry)
                self._column_exact.setdefault(column.name, []).append(entry)
                self._column_lower.setdefault(column_lower, []).append(entry)
                if column_lower not in self._column_name_order:
                    self._column_name_order[column_lower] = len(self._column_names)
                    self._column_names.append(column_lower)
                if column.comment:
                    self._column_comments.append(
                        (column_order, table_name, column.name, column.comment.lower())
                    )
                column_order += 1

        self._column_blob, self._column_starts = _blob(self._column_names)
        self._table_blob, self._table_starts = _blob(self._table_lower)
        self._part_names = list(self._part_tables)
        self._part_blob, self._part_starts = _blob(self._part_names)

    # ------------------------------------------------------------------
    # Table matching
    # ------------------------------------------------------------------

    def match_tables(self, name: str, threshold: float) -> list[tuple[str, float]]:
        """Return every table matching *name* at or above *threshold*.

        Mirrors the previous rules: exact 1.0, case-insensitive 0.95, comment
        containment 0.7, substring either way 0.65.  Results are ordered by
        descending confidence and then by schema order, as before.
        """
        name_lower = name.lower().strip()
        hits: dict[int, float] = {}

        def offer(order: int, confidence: float) -> None:
            if confidence >= threshold and confidence > hits.get(order, 0.0):
                hits[order] = confidence

        exact = self._table_order.get(name)
        if exact is not None:
            offer(exact, 1.0)
        for order in self._table_lower_index.get(name_lower, ()):
            offer(order, 0.95)
        for order, comment_lower in self._table_comments:
            if name_lower in comment_lower:
                offer(order, 0.7)

        for index in self._substring_table_indexes(name_lower):
            offer(index, 0.65)

        if threshold <= 0.0:
            # The old scan kept every table as an unmatched (0.0) candidate at
            # this threshold; keep that so a caller can see the full ranking.
            for order in range(len(self._table_names)):
                hits.setdefault(order, 0.0)

        return [
            (self._table_names[order], confidence)
            for order, confidence in sorted(hits.items(), key=lambda item: (-item[1], item[0]))
        ]

    def _substring_table_indexes(self, name_lower: str) -> list[int]:
        """Tables whose lower name and *name_lower* contain one another."""
        if not name_lower:
            return list(range(len(self._table_names)))
        found: list[int] = []
        seen: set[int] = set()
        for index in _positions_name_index(self._table_blob, self._table_starts, name_lower):
            seen.add(index)
            found.append(index)
        for candidate in self._substring_lookups(name_lower):
            for index in self._table_lower_index.get(candidate, ()):
                if index not in seen:
                    seen.add(index)
                    found.append(index)
        return found

    # ------------------------------------------------------------------
    # Column matching
    # ------------------------------------------------------------------

    def match_columns(self, name: str, threshold: float) -> list[tuple[str, str, float]]:
        """Return every matching column as ``(table, column, confidence)``.

        Mirrors the previous rules: exact 1.0, case-insensitive 0.95, comment
        containment 0.75, candidate inside the column name 0.65, column name
        inside the candidate 0.6.
        """
        name_lower = name.lower().strip()
        hits: dict[int, tuple[str, str, float]] = {}

        def offer(order: int, table: str, column: str, confidence: float) -> None:
            if confidence < threshold:
                return
            current = hits.get(order)
            if current is None or confidence > current[2]:
                hits[order] = (table, column, confidence)

        for order, table, column in self._column_exact.get(name, ()):
            offer(order, table, column, 1.0)
        for order, table, column in self._column_lower.get(name_lower, ()):
            offer(order, table, column, 0.95)
        for order, table, column, comment_lower in self._column_comments:
            if name_lower in comment_lower:
                offer(order, table, column, 0.75)

        for index in self._substring_column_names(name_lower):
            for order, table, column in self._column_lower[self._column_names[index]]:
                offer(order, table, column, 0.65)
        for candidate in self._substring_lookups(name_lower):
            for order, table, column in self._column_lower.get(candidate, ()):
                offer(order, table, column, 0.6)

        if threshold <= 0.0:
            for order, table, column in self._column_order:
                hits.setdefault(order, (table, column, 0.0))

        return [
            entry
            for _, entry in sorted(hits.items(), key=lambda item: (-item[1][2], item[0]))
        ]

    def _substring_column_names(self, name_lower: str) -> list[int]:
        """Indexes of unique column names that contain *name_lower*."""
        if not name_lower:
            return list(range(len(self._column_names)))
        return _positions_name_index(self._column_blob, self._column_starts, name_lower)

    def _substring_lookups(self, name_lower: str) -> Iterator[str]:
        """Substrings of *name_lower* that could be a whole column/table name."""
        if not name_lower:
            return iter(())
        if len(name_lower) <= _MAX_SUBSTRING_NEEDLE:
            return _substrings(name_lower)
        return iter(())
    # ------------------------------------------------------------------
    # Reverse table-name scan
    # ------------------------------------------------------------------

    def tables_sharing_parts(
        self,
        query_text: str,
        query_tokens: Iterable[str],
        reverse_map: Mapping[str, Sequence[str]],
    ) -> list[str]:
        """Tables whose name parts relate to *query_text*.

        Replaces the old per-table, per-part scan with the inverse direction:
        work out which parts can match the query (a handful of tokens) and then
        union the tables that contain them.  The three original rules are kept
        verbatim, including the length cut-offs.
        """
        if not query_text:
            return []

        query_lower = query_text.lower()
        tokens_lower = {token.lower() for token in query_tokens}
        matched_parts: set[str] = set()

        for part, zh_terms in reverse_map.items():
            if part not in self._part_tables:
                continue
            for zh in zh_terms:
                if zh in query_text or zh.lower() in tokens_lower:
                    matched_parts.add(part)
                    break

        if len(query_lower) <= _MAX_SUBSTRING_NEEDLE:
            for candidate in _substrings(query_lower):
                if len(candidate) < 4:
                    continue
                if candidate in self._part_tables:
                    matched_parts.add(candidate)
        else:
            # Absurdly long query: scan the (much smaller) part vocabulary instead.
            for part in self._part_tables:
                if len(part) >= 4 and part in query_lower:
                    matched_parts.add(part)

        for token in tokens_lower:
            if len(token) < 4:
                continue
            if token in self._part_tables:
                matched_parts.add(token)
            for index in _positions_name_index(self._part_blob, self._part_starts, token):
                matched_parts.add(self._part_names[index])
            for candidate in _substrings(token):
                if len(candidate) < 3:
                    continue
                if candidate in self._part_tables:
                    matched_parts.add(candidate)

        orders: set[int] = set()
        for part in matched_parts:
            orders.update(self._part_tables[part])
        return [self._table_names[order] for order in sorted(orders)]

    # ------------------------------------------------------------------
    # Direct keyword scan fallback
    # ------------------------------------------------------------------

    def direct_keyword_scan(self, tokens: Sequence[str]) -> dict[str, list[str]]:
        """Match query tokens straight against table and column names.

        Same contract as the previous helper: returns ``{column_name: [tables]}``
        and an empty mapping when no token touches any table name, which the
        caller reads as "this query is not about this database".
        """
        normalised: list[str] = []
        for token in tokens:
            token_lower = token.lower().strip()
            if len(token_lower) >= 2 and token_lower not in normalised:
                normalised.append(token_lower)
        if not normalised:
            return {}

        if not self._any_table_matches(normalised):
            return {}

        result: dict[str, list[str]] = {}
        for token_lower in normalised:
            indexes = self._column_name_indexes_for_token(token_lower)
            entries: list[tuple[int, str, str]] = []
            for index in indexes:
                entries.extend(self._column_lower[self._column_names[index]])
            for _, table, column in sorted(entries):
                tables = result.setdefault(column, [])
                if table not in tables:
                    tables.append(table)
        return result

    def _any_table_matches(self, tokens: Sequence[str]) -> bool:
        for token_lower in tokens:
            if token_lower in self._table_lower_index:
                return True
            if len(token_lower) < 3:
                continue
            if _positions_name_index(self._table_blob, self._table_starts, token_lower):
                return True
            # Table name contained in the token; the old rule required the
            # table name itself to be at least three characters long.
            for candidate in _substrings(token_lower):
                if len(candidate) >= 3 and candidate in self._table_lower_index:
                    return True
        return False

    def _column_name_indexes_for_token(self, token_lower: str) -> list[int]:
        indexes = self._substring_column_names(token_lower)
        if len(token_lower) >= 3:
            return indexes
        # Below three characters the old fallback only accepted exact names.
        exact = self._column_name_order.get(token_lower)
        return [] if exact is None else [exact]


def build_index(schema: SchemaSnapshot | None) -> SchemaNameIndex | None:
    """Build an index for *schema* when one is available."""
    if schema is None:
        return None
    return SchemaNameIndex(schema)
