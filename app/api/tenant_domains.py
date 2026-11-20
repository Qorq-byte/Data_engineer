"""MySQL domain administration for tenants with a persisted catalog."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from aiomysql import IntegrityError
from fastapi import HTTPException
from pymysql.err import DataError  # type: ignore[import-untyped]

from app.auth.tenant import DEFAULT_TENANT_ID, get_current_tenant_id
from app.harness.shared_query_runtime import current_tenant_release_snapshot
from app.knowledge.domain_manager import DomainManager
from app.knowledge.domain_snapshot import domain_from_snapshot
from app.models.domain import DomainMatch
from app.storage.domain_catalog import (
    DomainCatalogConflictError,
    DomainCatalogNotFoundError,
    DomainItemKind,
    TenantDomainCatalog,
)
from app.storage.mysql_store import MySQLStore, get_store


def uses_mysql_catalog() -> bool:
    return get_current_tenant_id() != DEFAULT_TENANT_ID


@asynccontextmanager
async def _catalog() -> AsyncIterator[MySQLStore]:
    store = get_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Domain catalog unavailable")
    try:
        yield store
    except DomainCatalogNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except DomainCatalogConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except IntegrityError as exc:
        if exc.args and exc.args[0] == 1062:
            raise HTTPException(
                status_code=409, detail="Catalog identifier already exists",
            ) from None
        raise HTTPException(status_code=503, detail="Domain catalog unavailable") from None
    except DataError:
        raise HTTPException(status_code=422, detail="Invalid catalog field value") from None
    except Exception:
        raise HTTPException(status_code=503, detail="Domain catalog unavailable") from None


async def _list_snapshots() -> list[dict[str, Any]]:
    release = current_tenant_release_snapshot()
    if release is not None:
        return list(release.domains.values())
    async with _catalog() as store:
        return await store.get_domain_snapshots()


async def list_domains() -> dict[str, Any]:
    rows = await _list_snapshots()
    domains = []
    for row in rows:
        domain = domain_from_snapshot(row)
        domains.append({
            "name": domain.name, "label": domain.label, "description": domain.description,
            "keywords": domain.keywords, "databases": domain.databases,
            "timezone": domain.timezone, "currency": domain.currency,
            "glossary_count": len(domain.glossary), "rules_count": len(domain.rules),
            "is_active": bool(row["is_active"]),
        })
    active = next((entry["name"] for entry in domains if entry["is_active"]), None)
    return {"domains": domains, "active": active, "total": len(domains)}


async def create_domain(name: str, fields: dict[str, Any]) -> dict[str, Any]:
    async with _catalog() as store:
        await store.save_domain({
            **fields, "name": name, "label_zh": fields["label_zh"] or name,
            "label_en": fields["label_en"] or name,
        }, create_only=True)
    return {"status": "created", "name": name}


async def update_domain(name: str, fields: dict[str, Any]) -> dict[str, Any]:
    async with _catalog() as store:
        row = await TenantDomainCatalog(store).update_domain(name, fields)
        keywords = row["keywords"]
        return {
            "status": "updated", "name": row["name"],
            "label": {"zh": row["label_zh"] or "", "en": row["label_en"] or ""},
            "keywords": (json.loads(keywords) if isinstance(keywords, str) else keywords) or [],
        }


async def delete_domain(name: str) -> dict[str, Any]:
    async with _catalog() as store:
        if not await store.delete_domain(name):
            raise DomainCatalogNotFoundError(f"Domain '{name}' not found")
    return {"status": "deleted", "domain": name}


async def list_items(domain_id: str, kind: DomainItemKind) -> dict[str, Any]:
    release = current_tenant_release_snapshot()
    if release is not None:
        row = release.domains.get(domain_id.casefold())
    else:
        async with _catalog() as store:
            row = await store.get_domain_snapshot(domain_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found")
    domain = domain_from_snapshot(row)
    if kind == "term":
        field = "terms"
        entries = [asdict(term) for term in domain.glossary]
    else:
        field = "rules"
        entries = [
            {key: value for key, value in asdict(rule).items() if key != "domain_id"}
            for rule in domain.rules
        ]
    return {"domain": domain.name, field: entries, "total": len(entries)}


async def write_item(
    domain_id: str, kind: DomainItemKind, fields: dict[str, Any] | None,
    *, current_key: str | None = None,
) -> dict[str, Any]:
    async with _catalog() as store:
        domain = await TenantDomainCatalog(store).write_item(
            domain_id, kind, fields, current_key=current_key,
        )
    if fields is None:
        status, item_id = "deleted", current_key
    else:
        status = "updated" if current_key is not None else "created"
        item_id = fields["term" if kind == "term" else "id"]
    return {"status": status, "domain": domain, "term" if kind == "term" else "rule_id": item_id}


async def detect_domains(query_text: str) -> list[DomainMatch]:
    rows = await _list_snapshots()
    return DomainManager.detect_domains((domain_from_snapshot(row) for row in rows), query_text)
