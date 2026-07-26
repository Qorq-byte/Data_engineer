"""Domain management API endpoints.

See SPEC §6.3 (Domain management API) for the full specification.

Phase 3: Full CRUD — 8 endpoints for domain, glossary, and rule management.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.knowledge.domain_manager import DomainManager
from app.knowledge.glossary import GlossaryManager
from app.knowledge.rule_engine import RuleEngine
from app.models.domain import BusinessRule, GlossaryTerm, TermMapping
from app.storage.mysql_store import get_store, MySQLStore

router = APIRouter()

# ── Module-level singletons ──────────────────────────────────────────────

DOMAINS_DIR = Path("app/config/domains")

_domain_manager = DomainManager(str(DOMAINS_DIR), auto_load=True)
_glossary_manager = GlossaryManager(domain_manager=_domain_manager)
_rule_engine = RuleEngine(domain_manager=_domain_manager)

# Load glossary and rules from domain YAML files on startup
_glossary_manager.load_from_domains()
_rule_engine.load_from_domains()


# ── YAML persistence helper ──────────────────────────────────────────────


def _persist_domain_yaml(domain_id: str) -> None:
    """Persist the current in-memory domain state (glossary + rules) back to YAML."""
    yaml_path = DOMAINS_DIR / f"{domain_id}.yml"
    if not yaml_path.exists():
        return

    try:
        with open(yaml_path, encoding="utf-8") as f:
            current: dict[str, Any] = yaml.safe_load(f) or {}
    except OSError:
        return

    # Serialize glossary terms
    terms = _glossary_manager.list_all(domain=domain_id)
    current["glossary"] = []
    for t in terms:
        g_entry: dict[str, Any] = {
            "term": t.term,
            "term_en": t.term_en,
            "description": t.description,
        }
        if t.mapping:
            g_entry["mapping"] = {
                "expression": t.mapping.expression,
                "type": t.mapping.type,
            }
            if t.mapping.table:
                g_entry["mapping"]["table"] = t.mapping.table
            if t.mapping.condition:
                g_entry["mapping"]["condition"] = t.mapping.condition
            if t.mapping.precision is not None:
                g_entry["mapping"]["precision"] = t.mapping.precision
        if t.tags:
            g_entry["tags"] = t.tags
        current["glossary"].append(g_entry)

    # Serialize rules
    rules = _rule_engine.list_all(domain=domain_id)
    current["rules"] = []
    for r in rules:
        r_entry: dict[str, Any] = {
            "id": r.id,
            "description": r.description,
        }
        if r.pattern:
            r_entry["pattern"] = r.pattern
        if r.enforce:
            r_entry["enforce"] = r.enforce
        if r.sql_template:
            r_entry["sql_template"] = r.sql_template
        current["rules"].append(r_entry)

    try:
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(current, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except OSError:
        pass


# ── MySQL store helper ───────────────────────────────────────────────────


def _get_store() -> MySQLStore | None:
    """Return the singleton MySQLStore instance, or None if not available."""
    return get_store()


# ── Request / Response models ────────────────────────────────────────────


class DomainCreateRequest(BaseModel):
    """Request body for POST /api/v1/domains — create a new domain."""

    name: str = Field(..., description="Domain identifier (e.g., 'healthcare')", min_length=1)
    label_zh: str = Field(default="", description="Chinese label")
    label_en: str = Field(default="", description="English label")
    description_zh: str = Field(default="", description="Chinese description")
    description_en: str = Field(default="", description="English description")
    databases: list[dict[str, Any]] = Field(default_factory=list, description="Database configs")
    keywords: list[str] = Field(default_factory=list, description="Domain keywords (zh + en)")
    timezone: str = Field(default="UTC")
    currency: str = Field(default="USD")


class DomainUpdateRequest(BaseModel):
    """Request body for PUT /api/v1/domains/{id} — update a domain."""

    label_zh: str | None = None
    label_en: str | None = None
    description_zh: str | None = None
    description_en: str | None = None
    databases: list[dict[str, Any]] | None = None
    keywords: list[str] | None = None
    timezone: str | None = None
    currency: str | None = None


class TermAddRequest(BaseModel):
    """Request body for POST /api/v1/domains/{id}/glossary — add a term."""

    term: str = Field(..., min_length=1, description="Business term name (zh)")
    term_en: str = Field(default="", description="English term name")
    description: str = Field(default="", description="Term description")
    expression: str = Field(default="", description="SQL expression")
    mapping_type: str = Field(
        default="derived_column",
        description="Mapping type: derived_column, filter_condition, or table_ref",
    )
    table: str | None = None
    condition: str | None = None
    precision: int | None = None
    tags: list[str] = Field(default_factory=list)


class RuleAddRequest(BaseModel):
    """Request body for POST /api/v1/domains/{id}/rules — add a rule."""

    id: str = Field(..., min_length=1, description="Unique rule identifier")
    description: str = Field(default="", description="Rule description")
    pattern: str = Field(default="", description="Regex pattern for matching")
    enforce: list[str] = Field(default_factory=list, description="Enforcement directives")
    sql_template: str = Field(default="", description="SQL template for the rule")


class DetectRequest(BaseModel):
    """Request body for POST /api/v1/domains/detect — auto-detect domain."""

    query_text: str = Field(..., min_length=1, description="NL query text to classify")


# ── GET /domains — list all available domains ────────────────────────────


@router.get("/domains")
async def list_domains() -> dict[str, Any]:
    """List all available domains with their configurations."""
    # Try MySQL first
    store = _get_store()
    if store is not None:
        try:
            mysql_domains = await store.get_domains()
            domains: list[dict[str, Any]] = []
            for row in mysql_domains:
                domains.append({
                    "name": row.get("name", ""),
                    "label": {"zh": row.get("label_zh", ""), "en": row.get("label_en", "")},
                    "description": {"zh": row.get("description_zh", ""), "en": row.get("description_en", "")},
                    "keywords": row.get("keywords", []),
                    "databases": [],
                    "timezone": row.get("timezone", "UTC"),
                    "currency": row.get("currency", "USD"),
                    "glossary_count": 0,
                    "rules_count": 0,
                    "is_active": bool(row.get("is_active", False)),
                })
            active_row = next((d for d in domains if d["is_active"]), None)
            return {
                "domains": domains,
                "active": active_row["name"] if active_row else _domain_manager.active_name,
                "total": len(domains),
            }
        except Exception:
            pass  # Fall through to YAML

    # Fallback to YAML
    domains: list[dict[str, Any]] = []
    for name in sorted(_domain_manager.list_all()):
        cfg = _domain_manager.get(name)
        if cfg is None:
            continue
        domains.append({
            "name": cfg.name,
            "label": cfg.label,
            "description": cfg.description,
            "keywords": cfg.keywords,
            "databases": cfg.databases,
            "timezone": cfg.timezone,
            "currency": cfg.currency,
            "glossary_count": len(cfg.glossary),
            "rules_count": len(cfg.rules),
            "is_active": _domain_manager.active_name == name,
        })

    return {
        "domains": domains,
        "active": _domain_manager.active_name,
        "total": len(domains),
    }


# ── POST /domains — create a new domain ──────────────────────────────────


@router.post("/domains", status_code=201)
async def create_domain(req: DomainCreateRequest) -> dict[str, Any]:
    """Create a new domain and persist it as a YAML file."""
    name = req.name.strip()

    # Check for duplicates
    if name in _domain_manager:
        raise HTTPException(status_code=409, detail=f"Domain '{name}' already exists")

    # Build domain YAML structure
    domain_data: dict[str, Any] = {
        "name": name,
        "label": {"zh": req.label_zh or name, "en": req.label_en or name},
        "description": {
            "zh": req.description_zh or "",
            "en": req.description_en or "",
        },
        "databases": req.databases,
        "keywords": req.keywords,
        "timezone": req.timezone,
        "currency": req.currency,
        "glossary": [],
        "rules": [],
    }

    # Write to disk
    yaml_path = DOMAINS_DIR / f"{name}.yml"
    try:
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(domain_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write domain file: {e}") from e

    # Reload
    _domain_manager.reload()

    # Persist to MySQL
    store = _get_store()
    if store is not None:
        try:
            await store.save_domain({
                "name": name,
                "label_zh": req.label_zh or name,
                "label_en": req.label_en or name,
                "description_zh": req.description_zh or "",
                "description_en": req.description_en or "",
                "keywords": req.keywords,
                "timezone": req.timezone,
                "currency": req.currency,
                "is_active": False,
            })
        except Exception:
            pass

    return {"status": "created", "name": name, "path": str(yaml_path)}


# ── PUT /domains/{id} — update a domain ──────────────────────────────────


@router.put("/domains/{domain_id}")
async def update_domain(domain_id: str, req: DomainUpdateRequest) -> dict[str, Any]:
    """Update an existing domain's configuration.

    Only supplied fields are updated; omitted fields keep their current values.
    """
    domain_id = domain_id.strip()

    cfg = _domain_manager.get(domain_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found")

    yaml_path = DOMAINS_DIR / f"{domain_id}.yml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"Domain file for '{domain_id}' not found")

    # Read current YAML
    try:
        with open(yaml_path, encoding="utf-8") as f:
            current: dict[str, Any] = yaml.safe_load(f) or {}
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to read domain file: {e}") from e

    # Apply partial updates
    if req.label_zh is not None:
        current.setdefault("label", {})["zh"] = req.label_zh
    if req.label_en is not None:
        current.setdefault("label", {})["en"] = req.label_en
    if req.description_zh is not None:
        current.setdefault("description", {})["zh"] = req.description_zh
    if req.description_en is not None:
        current.setdefault("description", {})["en"] = req.description_en
    if req.databases is not None:
        current["databases"] = req.databases
    if req.keywords is not None:
        current["keywords"] = req.keywords
    if req.timezone is not None:
        current["timezone"] = req.timezone
    if req.currency is not None:
        current["currency"] = req.currency

    # Write back
    try:
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(current, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write domain file: {e}") from e

    # Reload
    _domain_manager.reload()

    # Persist to MySQL
    store = _get_store()
    if store is not None:
        try:
            await store.save_domain({
                "name": domain_id,
                "label_zh": current.get("label", {}).get("zh", ""),
                "label_en": current.get("label", {}).get("en", ""),
                "description_zh": current.get("description", {}).get("zh", ""),
                "description_en": current.get("description", {}).get("en", ""),
                "keywords": current.get("keywords", []),
                "timezone": current.get("timezone", "Asia/Shanghai"),
                "currency": current.get("currency", "CNY"),
                "is_active": _domain_manager.active_name == domain_id,
            })
        except Exception:
            pass

    updated = _domain_manager.get(domain_id)
    return {
        "status": "updated",
        "name": domain_id,
        "label": updated.label if updated else {},
        "keywords": updated.keywords if updated else [],
    }


# ── GET /domains/{id}/glossary — list glossary terms ─────────────────────


@router.get("/domains/{domain_id}/glossary")
async def list_glossary(domain_id: str) -> dict[str, Any]:
    """List all glossary terms for a domain."""
    domain_id = domain_id.strip()

    if domain_id not in _domain_manager:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found")

    # Try MySQL first
    store = _get_store()
    if store is not None:
        try:
            mysql_terms = await store.get_glossary_terms(domain_id)
            if mysql_terms:
                return {
                    "domain": domain_id,
                    "terms": [
                        {
                            "term": t.get("term", ""),
                            "term_en": t.get("term_en", ""),
                            "description": t.get("description", ""),
                            "mapping": {
                                "expression": t.get("expression", ""),
                                "type": t.get("mapping_type", "derived_column"),
                                "table": "",
                            } if t.get("expression") else None,
                            "tags": t.get("tags", []),
                        }
                        for t in mysql_terms
                    ],
                    "total": len(mysql_terms),
                }
        except Exception:
            pass

    terms = _glossary_manager.list_all(domain=domain_id)
    return {
        "domain": domain_id,
        "terms": [
            {
                "term": t.term,
                "term_en": t.term_en,
                "description": t.description,
                "mapping": {
                    "expression": t.mapping.expression,
                    "type": t.mapping.type,
                    "table": t.mapping.table,
                } if t.mapping else None,
                "tags": t.tags,
            }
            for t in terms
        ],
        "total": len(terms),
    }


# ── POST /domains/{id}/glossary — add a glossary term ────────────────────


@router.post("/domains/{domain_id}/glossary", status_code=201)
async def add_glossary_term(domain_id: str, req: TermAddRequest) -> dict[str, Any]:
    """Add a new glossary term to a domain."""
    domain_id = domain_id.strip()

    if domain_id not in _domain_manager:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found")

    # Check for duplicate
    existing = _glossary_manager.get(req.term, domain=domain_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Term '{req.term}' already exists in domain '{domain_id}'",
        )

    # Build mapping if expression is provided
    mapping = None
    if req.expression:
        mapping = TermMapping(
            expression=req.expression,
            type=req.mapping_type,  # type: ignore
            table=req.table,
            condition=req.condition,
            precision=req.precision,
        )

    term = GlossaryTerm(
        term=req.term,
        term_en=req.term_en,
        description=req.description,
        mapping=mapping,
        tags=req.tags,
    )

    _glossary_manager.add(term, domain=domain_id)
    _persist_domain_yaml(domain_id)

    # Persist to MySQL
    store = _get_store()
    if store is not None:
        try:
            await store.save_glossary_term(domain_id, {
                "term": req.term,
                "term_en": req.term_en,
                "description": req.description,
                "expression": req.expression,
                "mapping_type": req.mapping_type,
                "tags": req.tags,
            })
        except Exception:
            pass

    # Sync to MetricRAG
    _sync_glossary_term_to_rag(term, domain_id)

    return {
        "status": "created",
        "domain": domain_id,
        "term": req.term,
    }


# ── PUT /domains/{id}/glossary/{term} — update a glossary term ───────────


@router.put("/domains/{domain_id}/glossary/{term}")
async def update_glossary_term(domain_id: str, term: str, req: TermAddRequest) -> dict[str, Any]:
    """Update an existing glossary term in a domain."""
    domain_id = domain_id.strip()
    term = term.strip()

    if domain_id not in _domain_manager:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found")

    existing = _glossary_manager.get(term, domain=domain_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Term '{term}' not found in domain '{domain_id}'",
        )

    # Build mapping if expression is provided
    mapping = None
    if req.expression:
        mapping = TermMapping(
            expression=req.expression,
            type=req.mapping_type,
            table=req.table,
            condition=req.condition,
            precision=req.precision,
        )

    updated_term = GlossaryTerm(
        term=req.term,
        term_en=req.term_en,
        description=req.description,
        mapping=mapping,
        tags=req.tags,
    )

    # If term name changed, delete old and add new
    if req.term != term:
        _glossary_manager.delete(term, domain=domain_id)
        _glossary_manager.add(updated_term, domain=domain_id)
    else:
        _glossary_manager.update(
            term,
            {
                "term_en": req.term_en,
                "description": req.description,
                "mapping": mapping,
                "tags": req.tags,
            },
            domain=domain_id,
        )

    _persist_domain_yaml(domain_id)

    # Persist to MySQL
    store = _get_store()
    if store is not None:
        try:
            if req.term != term:
                await store.delete_glossary_term(domain_id, term)
            await store.save_glossary_term(domain_id, {
                "term": req.term,
                "term_en": req.term_en,
                "description": req.description,
                "expression": req.expression,
                "mapping_type": req.mapping_type,
                "tags": req.tags,
            })
        except Exception:
            pass

    # Sync to MetricRAG
    if req.term != term:
        _remove_metric_from_rag(f"glossary.{domain_id}.{term}", domain_id)
    _sync_glossary_term_to_rag(updated_term, domain_id)

    return {
        "status": "updated",
        "domain": domain_id,
        "term": req.term,
    }


# ── DELETE /domains/{id} — delete a domain ───────────────────────────────


@router.delete("/domains/{domain_id}")
async def delete_domain(domain_id: str) -> dict[str, Any]:
    """Delete a domain and its YAML file."""
    domain_id = domain_id.strip()

    if domain_id not in _domain_manager:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found")

    yaml_path = DOMAINS_DIR / f"{domain_id}.yml"
    try:
        if yaml_path.exists():
            yaml_path.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete domain file: {e}") from e

    # Clear in-memory state
    _glossary_manager.clear(domain=domain_id)
    _rule_engine.clear(domain=domain_id)
    _domain_manager.reload()

    # Delete from MySQL
    store = _get_store()
    if store is not None:
        try:
            await store.delete_domain(domain_id)
        except Exception:
            pass

    # Clean up MetricRAG for this domain
    try:
        import contextlib

        from app.rag import get_metric_rag

        rag = get_metric_rag()
        with contextlib.suppress(Exception):
            rag._lancedb.delete("metrics", f"domain = '{domain_id}'")
        with contextlib.suppress(Exception):
            rag._bm25.delete_by_field("metrics", "domain", domain_id)
    except Exception:
        pass

    return {"status": "deleted", "domain": domain_id}


# ── DELETE /domains/{id}/glossary/{term} — delete a glossary term ────────


@router.delete("/domains/{domain_id}/glossary/{term}")
async def delete_glossary_term(domain_id: str, term: str) -> dict[str, Any]:
    """Delete a glossary term from a domain and persist to YAML."""
    domain_id = domain_id.strip()
    term = term.strip()

    if domain_id not in _domain_manager:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found")

    existing = _glossary_manager.get(term, domain=domain_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Term '{term}' not found in domain '{domain_id}'",
        )

    _glossary_manager.delete(term, domain=domain_id)
    _persist_domain_yaml(domain_id)

    # Delete from MySQL
    store = _get_store()
    if store is not None:
        try:
            await store.delete_glossary_term(domain_id, term)
        except Exception:
            pass

    # Remove from MetricRAG
    _remove_metric_from_rag(f"glossary.{domain_id}.{term}", domain_id)

    return {"status": "deleted", "domain": domain_id, "term": term}


# ── GET /domains/{id}/rules — list business rules ────────────────────────


@router.get("/domains/{domain_id}/rules")
async def list_rules(domain_id: str) -> dict[str, Any]:
    """List all business rules for a domain."""
    domain_id = domain_id.strip()

    if domain_id not in _domain_manager:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found")

    # Try MySQL first
    store = _get_store()
    if store is not None:
        try:
            mysql_rules = await store.get_rules(domain_id)
            if mysql_rules:
                return {
                    "domain": domain_id,
                    "rules": [
                        {
                            "id": r.get("rule_id", ""),
                            "description": r.get("description", ""),
                            "pattern": r.get("pattern", ""),
                            "enforce": r.get("enforce", []),
                            "sql_template": r.get("sql_template", ""),
                        }
                        for r in mysql_rules
                    ],
                    "total": len(mysql_rules),
                }
        except Exception:
            pass

    rules = _rule_engine.list_all(domain=domain_id)
    return {
        "domain": domain_id,
        "rules": [
            {
                "id": r.id,
                "description": r.description,
                "pattern": r.pattern,
                "enforce": r.enforce,
                "sql_template": r.sql_template,
            }
            for r in rules
        ],
        "total": len(rules),
    }


# ── POST /domains/{id}/rules — add a business rule ───────────────────────


@router.post("/domains/{domain_id}/rules", status_code=201)
async def add_rule(domain_id: str, req: RuleAddRequest) -> dict[str, Any]:
    """Add a new business rule to a domain."""
    domain_id = domain_id.strip()

    if domain_id not in _domain_manager:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found")

    # Check for duplicate
    existing = _rule_engine.get(req.id, domain=domain_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Rule '{req.id}' already exists in domain '{domain_id}'",
        )

    rule = BusinessRule(
        id=req.id,
        description=req.description,
        pattern=req.pattern,
        enforce=req.enforce,
        sql_template=req.sql_template,
        domain_id=domain_id,
    )

    _rule_engine.add(rule, domain=domain_id)
    _persist_domain_yaml(domain_id)

    # Persist to MySQL
    store = _get_store()
    if store is not None:
        try:
            await store.save_rule(domain_id, {
                "rule_id": req.id,
                "description": req.description,
                "pattern": req.pattern,
                "enforce": req.enforce,
                "sql_template": req.sql_template,
            })
        except Exception:
            pass

    # Sync to MetricRAG
    _sync_rule_to_rag(rule, domain_id)

    return {
        "status": "created",
        "domain": domain_id,
        "rule_id": req.id,
    }


# ── PUT /domains/{id}/rules/{rule_id} — update a business rule ───────────


@router.put("/domains/{domain_id}/rules/{rule_id}")
async def update_rule(domain_id: str, rule_id: str, req: RuleAddRequest) -> dict[str, Any]:
    """Update an existing business rule in a domain."""
    domain_id = domain_id.strip()
    rule_id = rule_id.strip()

    if domain_id not in _domain_manager:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found")

    existing = _rule_engine.get(rule_id, domain=domain_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Rule '{rule_id}' not found in domain '{domain_id}'",
        )

    updated_rule = BusinessRule(
        id=req.id,
        description=req.description,
        pattern=req.pattern,
        enforce=req.enforce,
        sql_template=req.sql_template,
        domain_id=domain_id,
    )

    # If rule ID changed, delete old and add new
    if req.id != rule_id:
        _rule_engine.delete(rule_id, domain=domain_id)
        _rule_engine.add(updated_rule, domain=domain_id)
    else:
        _rule_engine.update(
            rule_id,
            {
                "description": req.description,
                "pattern": req.pattern,
                "enforce": req.enforce,
                "sql_template": req.sql_template,
            },
            domain=domain_id,
        )

    _persist_domain_yaml(domain_id)

    # Persist to MySQL
    store = _get_store()
    if store is not None:
        try:
            if req.id != rule_id:
                await store.delete_rule(domain_id, rule_id)
            await store.save_rule(domain_id, {
                "rule_id": req.id,
                "description": req.description,
                "pattern": req.pattern,
                "enforce": req.enforce,
                "sql_template": req.sql_template,
            })
        except Exception:
            pass

    # Sync to MetricRAG
    if req.id != rule_id:
        _remove_metric_from_rag(f"rule.{domain_id}.{rule_id}", domain_id)
    _sync_rule_to_rag(updated_rule, domain_id)

    return {
        "status": "updated",
        "domain": domain_id,
        "rule_id": req.id,
    }


# ── DELETE /domains/{id}/rules/{rule_id} — delete a business rule ────────


@router.delete("/domains/{domain_id}/rules/{rule_id}")
async def delete_rule(domain_id: str, rule_id: str) -> dict[str, Any]:
    """Delete a business rule from a domain."""
    domain_id = domain_id.strip()
    rule_id = rule_id.strip()

    if domain_id not in _domain_manager:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found")

    existing = _rule_engine.get(rule_id, domain=domain_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Rule '{rule_id}' not found in domain '{domain_id}'",
        )

    _rule_engine.delete(rule_id, domain=domain_id)
    _persist_domain_yaml(domain_id)

    # Delete from MySQL
    store = _get_store()
    if store is not None:
        try:
            await store.delete_rule(domain_id, rule_id)
        except Exception:
            pass

    # Remove from MetricRAG
    _remove_metric_from_rag(f"rule.{domain_id}.{rule_id}", domain_id)

    return {"status": "deleted", "domain": domain_id, "rule_id": rule_id}


# ── POST /domains/detect — auto-detect domain ────────────────────────────


@router.post("/domains/detect")
async def detect_domain(req: DetectRequest) -> dict[str, Any]:
    """Auto-detect the best domain for a given NL query.

    Uses the 3-layer detection strategy (keywords → terms → schema)
    and returns the best match if confidence >= 0.6.
    """
    matches = _domain_manager.detect(req.query_text)
    best = _domain_manager.detect_best(req.query_text)

    return {
        "query_text": req.query_text,
        "matches": [
            {
                "domain": m.domain.name,
                "score": round(m.score, 4),
                "matched_keywords": m.matched_keywords,
                "matched_terms": m.matched_terms,
            }
            for m in matches
        ],
        "best": {
            "domain": best.domain.name,
            "score": round(best.score, 4),
            "matched_keywords": best.matched_keywords,
            "matched_terms": best.matched_terms,
        } if best else None,
        "recommendation": best.domain.name if best and best.score >= 0.6 else None,
    }


# ── MetricRAG sync helpers ─────────────────────────────────────────────────


def _sync_glossary_term_to_rag(term: Any, domain_id: str) -> None:
    """Index a glossary term into MetricRAG (fire-and-forget, best-effort)."""
    try:
        from app.rag.converters import glossary_term_to_metric_doc
        from app.rag import get_metric_rag

        metric_doc = glossary_term_to_metric_doc(term, domain_id)
        import asyncio

        async def _index() -> None:
            import contextlib

            with contextlib.suppress(Exception):
                await get_metric_rag().index_metric(metric_doc)

        asyncio.create_task(_index())
    except Exception:
        pass


def _sync_rule_to_rag(rule: Any, domain_id: str) -> None:
    """Index a business rule into MetricRAG (fire-and-forget, best-effort)."""
    try:
        from app.rag.converters import business_rule_to_metric_doc
        from app.rag import get_metric_rag

        metric_doc = business_rule_to_metric_doc(rule, domain_id)
        import asyncio

        async def _index() -> None:
            import contextlib

            with contextlib.suppress(Exception):
                await get_metric_rag().index_metric(metric_doc)

        asyncio.create_task(_index())
    except Exception:
        pass


def _remove_metric_from_rag(doc_id: str, domain_id: str) -> None:
    """Remove a metric document from both LanceDB and BM25 (best-effort)."""
    try:
        import contextlib

        from app.rag import get_metric_rag

        rag = get_metric_rag()
        with contextlib.suppress(Exception):
            rag._lancedb.delete("metrics", f"doc_id = '{doc_id}'")
        with contextlib.suppress(Exception):
            rag._bm25.delete_by_field("metrics", "doc_id", doc_id)
    except Exception:
        pass
