"""Resolve the domain knowledge used by a query in the current tenant."""

from app.auth.tenant import DEFAULT_TENANT_ID, get_current_tenant_id
from app.harness.shared_query_runtime import current_tenant_release_snapshot
from app.knowledge.domain_snapshot import domain_from_snapshot
from app.models.domain import DomainConfig
from app.storage.mysql_store import get_store
from app.workflow.contracts import QueryDomainUnavailableError, QueryPlanError


async def resolve_query_domain(name: str) -> DomainConfig | None:
    if not name:
        return None
    if get_current_tenant_id() == DEFAULT_TENANT_ID:
        from app.api.domains import _domain_manager

        return _domain_manager.get(name)

    try:
        release = current_tenant_release_snapshot()
        if release is not None:
            row = release.domains.get(name.casefold())
        else:
            store = get_store()
            if store is None:
                raise QueryDomainUnavailableError()
            row = await store.get_domain_snapshot(name)
        if row is not None:
            return domain_from_snapshot(row)
    except Exception as exc:
        raise QueryDomainUnavailableError() from exc
    if name != "default":
        raise QueryPlanError("Domain not found for this tenant")
    return None
