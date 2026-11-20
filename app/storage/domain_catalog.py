"""Transactional editing of the current tenant's persisted domain catalog."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

import aiomysql

from app.auth.tenant import get_current_tenant_id
from app.storage.mysql_store import MySQLStore

type DomainItemKind = Literal["term", "rule"]


class DomainCatalogNotFoundError(LookupError):
    """The requested domain or item is absent from the current tenant."""


class DomainCatalogConflictError(ValueError):
    """An item with the requested identifier already exists."""


_DOMAIN_FIELDS = {
    "label_zh": "label_zh", "label_en": "label_en",
    "description_zh": "description_zh", "description_en": "description_en",
    "keywords": "keywords", "databases": "db_configs", "timezone": "timezone",
    "currency": "currency",
}


@dataclass(frozen=True)
class _ItemTable:
    name: str
    key: str
    fields: dict[str, str]


_ITEMS: dict[DomainItemKind, _ItemTable] = {
    "term": _ItemTable("glossary_terms", "term", {
        "term": "term", "term_en": "term_en", "description": "description",
        "expression": "expression", "mapping_type": "mapping_type", "table": "mapping_table",
        "condition": "mapping_condition", "precision": "mapping_precision", "tags": "tags",
    }),
    "rule": _ItemTable("domain_rules", "rule_id", {
        "id": "rule_id", "description": "description", "pattern": "pattern",
        "enforce": "enforce", "sql_template": "sql_template",
    }),
}
_JSON_COLUMNS = {"keywords", "db_configs", "tags", "enforce"}


def _column_values(fields: dict[str, Any], columns: dict[str, str]) -> dict[str, Any]:
    translated = {columns[name]: value for name, value in fields.items()}
    return {
        column: json.dumps(value, ensure_ascii=False) if column in _JSON_COLUMNS else value
        for column, value in translated.items()
    }


class TenantDomainCatalog:
    """Keep partial updates and child renames atomic, including across workers."""

    def __init__(self, store: MySQLStore) -> None:
        self._store = store

    @asynccontextmanager
    async def _locked_domain(
        self, name: str,
    ) -> AsyncIterator[tuple[aiomysql.DictCursor, int, dict[str, Any]]]:
        tenant_id = get_current_tenant_id()
        async with self._store._pool.acquire() as conn:
            try:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute("START TRANSACTION")
                    await cursor.execute(
                        "SELECT * FROM domains WHERE tenant_id = %s AND name = %s FOR UPDATE",
                        (tenant_id, name),
                    )
                    domain = await cursor.fetchone()
                    if domain is None:
                        raise DomainCatalogNotFoundError(f"Domain '{name}' not found")
                    yield cursor, tenant_id, domain
                await conn.commit()
            except BaseException:
                conn.close()  # Roll back and discard even when a transaction is cancelled.
                raise

    async def update_domain(self, name: str, fields: dict[str, Any]) -> dict[str, Any]:
        values = _column_values(fields, _DOMAIN_FIELDS)
        async with self._locked_domain(name) as (cursor, tenant_id, domain):
            if values:
                assignments = ", ".join(f"`{column}` = %s" for column in values)
                await cursor.execute(
                    f"UPDATE domains SET {assignments} WHERE tenant_id = %s AND name = %s",
                    (*values.values(), tenant_id, domain["name"]),
                )
            domain.update(values)
        return domain

    async def write_item(
        self, domain_name: str, kind: DomainItemKind, fields: dict[str, Any] | None,
        *, current_key: str | None = None,
    ) -> str:
        """Insert an item, update one by its old identifier, or delete it."""
        item = _ITEMS[kind]
        owner = "tenant_id = %s AND domain_name = %s"
        async with self._locked_domain(domain_name) as (cursor, tenant_id, domain):
            scope = (tenant_id, domain["name"])
            row_id = None
            if current_key is not None:
                await cursor.execute(
                    f"SELECT id FROM {item.name} WHERE {owner} AND {item.key} = %s "
                    "ORDER BY id LIMIT 1",
                    (*scope, current_key),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise DomainCatalogNotFoundError(
                        f"{kind.capitalize()} '{current_key}' not found",
                    )
                row_id = row["id"]

            if fields is None:
                if row_id is None:
                    raise ValueError("An item identifier is required for deletion")
                await cursor.execute(
                    f"DELETE FROM {item.name} WHERE {owner} AND id = %s", (*scope, row_id),
                )
            else:
                values = _column_values(fields, item.fields)
                identifier = values[item.key]
                await cursor.execute(
                    f"SELECT id FROM {item.name} WHERE {owner} AND {item.key} = %s "
                    "AND id <> %s LIMIT 1",
                    (*scope, identifier, row_id or 0),
                )
                if await cursor.fetchone() is not None:
                    raise DomainCatalogConflictError(f"{kind.capitalize()} '{identifier}' exists")
                if row_id is None:
                    names = ", ".join(f"`{column}`" for column in values)
                    placeholders = ", ".join(["%s"] * (len(values) + 2))
                    await cursor.execute(
                        f"INSERT INTO {item.name} (tenant_id, domain_name, {names}) "
                        f"VALUES ({placeholders})",
                        (*scope, *values.values()),
                    )
                else:
                    assignments = ", ".join(f"`{column}` = %s" for column in values)
                    await cursor.execute(
                        f"UPDATE {item.name} SET {assignments} WHERE {owner} AND id = %s",
                        (*values.values(), *scope, row_id),
                    )
        return str(domain["name"])
