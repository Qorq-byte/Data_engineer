"""Load host-provisioned query targets for enrolled tenants."""

import asyncio
import json
from pathlib import Path
from typing import Any

from app.auth.tenant import DEFAULT_TENANT_ID, reset_current_tenant_id, set_current_tenant_id
from app.config.settings import settings
from app.db.connections import ConnectionFactory, DatabaseType
from app.harness.worker_release import validate_profiles


def _read_profiles(path: Path) -> dict[int, list[dict[str, Any]]]:
    if not path.is_absolute() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Invalid tenant profile file")
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, dict):
        raise ValueError("Invalid tenant profiles")
    result = {}
    for name, profiles in records.items():
        tenant = int(name)
        if (
            str(tenant) != name or tenant <= DEFAULT_TENANT_ID
            or not settings.tenant_admin_user_ids.get(tenant)
        ):
            raise ValueError("Tenant profiles require a configured tenant")
        result[tenant] = validate_profiles(profiles)
    return result


async def load_tenant_query_profiles() -> list[tuple[int, str]]:
    """Load validated deployment profiles before admitting requests."""
    if settings.shared_worker_release or not settings.tenant_query_profiles_path:
        return []
    opened: list[tuple[int, str]] = []
    try:
        records = await asyncio.to_thread(_read_profiles, Path(settings.tenant_query_profiles_path))
        for tenant, profiles in records.items():
            token = set_current_tenant_id(tenant)
            try:
                for profile in profiles:
                    db_id = profile["db_id"]
                    if any(info.db_id == db_id for info in ConnectionFactory.list_all()):
                        raise ValueError("Tenant profile has already been loaded")
                    await ConnectionFactory.create(
                        DatabaseType(profile["db_type"]), profile["config"], db_id,
                    )
                    opened.append((tenant, db_id))
            finally:
                reset_current_tenant_id(token)
    except BaseException as exc:
        await close_tenant_query_profiles(opened)
        if isinstance(exc, Exception):
            raise RuntimeError("Tenant query profiles unavailable") from None
        raise
    return opened


async def close_tenant_query_profiles(opened: list[tuple[int, str]]) -> None:
    for tenant, db_id in reversed(opened):
        token = set_current_tenant_id(tenant)
        try:
            await ConnectionFactory.aclose(db_id)
        finally:
            reset_current_tenant_id(token)
