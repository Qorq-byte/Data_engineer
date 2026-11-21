"""Explicit immutable multi-process startup; single-worker startup remains unchanged."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import aiomysql
from fastapi import FastAPI

from app.auth.tenant import DEFAULT_TENANT_ID, tenant_scope
from app.config.settings import settings
from app.db.connections import ConnectionFactory, DatabaseType
from app.harness.mysql_coordination import (
    CoordinationBusyError,
    CoordinationUnavailableError,
    MySQLCoordinator,
)
from app.harness.shared_query_runtime import (
    SharedQueryRuntime,
    TenantReleaseSnapshot,
    publish_shared_runtime,
)
from app.harness.worker_release import ROOT, WorkerRelease
from app.knowledge.domain_snapshot import domain_from_snapshot
from app.storage.mysql_store import MySQLStore, get_store
from app.storage.shared_sessions import MySQLSessionStore


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


async def _persisted_state(
    store: MySQLStore, tenants: frozenset[int],
) -> tuple[dict[str, Any], dict[int, TenantReleaseSnapshot]]:
    with tenant_scope(DEFAULT_TENANT_ID):
        default = await store.get_settings()
    snapshots = {}
    for tenant in sorted(tenants):
        with tenant_scope(tenant):
            persisted = await store.get_settings()
            rows = await store.get_domain_snapshots()
        for row in rows:
            row["glossary"] = sorted(row["glossary"] or [], key=lambda item: item["term"])
            row["rules"] = sorted(row["rules"] or [], key=lambda item: item["id"])
        snapshots[tenant] = TenantReleaseSnapshot(
            settings=persisted, domains={row["name"].casefold(): row for row in rows},
        )
    return default, snapshots


def _baseline_digest(state: tuple[dict[str, Any], dict[int, TenantReleaseSnapshot]]) -> str:
    default, snapshots = state
    if not snapshots:
        return _hash(default)
    return _hash({
        "default": default,
        "tenants": {
            tenant: {"settings": snapshot.settings, "domains": snapshot.domains}
            for tenant, snapshot in snapshots.items()
        },
    })


@asynccontextmanager
async def immutable_worker_lifespan(
    app: FastAPI,
    initialize: Callable[[FastAPI], AbstractAsyncContextManager[None]],
) -> AsyncIterator[None]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", settings.shared_worker_namespace):
        raise ValueError("Explicit immutable worker deployment namespace required")
    if (
        not 1 <= settings.max_concurrent_queries <= 64
        or not 1 <= settings.llm_max_concurrency <= 64
    ):
        raise ValueError("Shared query/model concurrency must be 1..64")
    release = await asyncio.to_thread(WorkerRelease, Path(settings.shared_worker_release))
    await asyncio.to_thread(release.catalog_for, settings.metric_catalog_path)
    if release.tenant_ids and (
        settings.tenant_query_profiles_path
        or set(settings.tenant_admin_user_ids) != release.tenant_ids
        or not all(settings.tenant_admin_user_ids.values())
        or not settings.tenant_llm_endpoints.keys() <= release.tenant_ids
    ):
        raise CoordinationUnavailableError("Tenant operator or endpoint inventory mismatch")
    required = {Path(settings.agent_config_path).resolve(), Path(settings.rag_bm25_path)}
    required.update((ROOT / "app/config").rglob("*.yml"))
    required.update((ROOT / "app/workflow/plans").rglob("*.yml"))
    dense = Path(settings.rag_lancedb_dir)
    if (
        not dense.is_absolute()
        or not Path(settings.rag_bm25_path).is_absolute()
        or not (dense / "embedding-version.json").is_file()
        or any(dense.parent.glob("index-pending-*.json"))
        or not {str(path.resolve()) for path in required} <= release.files.keys()
        or dense.resolve() not in release.index_roots
    ):
        raise CoordinationUnavailableError(
            "Immutable worker release does not cover configuration/indexes"
        )
    pool = await aiomysql.create_pool(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        db=settings.mysql_database,
        autocommit=True,
        minsize=1,
        maxsize=settings.max_concurrent_queries + settings.llm_max_concurrency + 3,
    )
    runtime = None
    from app.api.settings import (
        _load_tenant_runtime,
        _normalize_llm_configuration,
        _settings_store,
        _tenant_llm_runtimes,
    )

    prior_settings = copy.deepcopy(_settings_store)
    async with AsyncExitStack() as stack:
        try:
            coordinator = MySQLCoordinator(
                pool, settings.shared_worker_namespace, settings.max_concurrent_queries
            )
            await coordinator.initialize()
            # Serialize seed/default restoration and opening shared stores at startup.
            deadline = asyncio.get_running_loop().time() + 30
            while True:
                try:
                    async with coordinator.query("__immutable_worker_startup__"):
                        state = await _persisted_state(MySQLStore(pool), release.tenant_ids)
                        persisted, tenant_snapshots = state
                        baseline = _baseline_digest(state)
                        _settings_store.update(copy.deepcopy(persisted))
                        for tenant, profiles in release.profile_groups.items():
                            with tenant_scope(tenant):
                                if ConnectionFactory.list_all():
                                    raise CoordinationUnavailableError(
                                        "Immutable worker requires a fresh registry"
                                    )
                                for profile in profiles:
                                    await ConnectionFactory.create(
                                        DatabaseType(profile["db_type"]), profile["config"],
                                        profile["db_id"],
                                    )
                        for tenant, snapshot in tenant_snapshots.items():
                            with tenant_scope(tenant):
                                models = snapshot.settings.get("llm")
                                _load_tenant_runtime(
                                    _normalize_llm_configuration({} if models is None else models),
                                )
                                for row in snapshot.domains.values():
                                    domain_from_snapshot(row)
                        await stack.enter_async_context(initialize(app))
                        break
                except CoordinationBusyError:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise CoordinationUnavailableError(
                            "Immutable worker startup capacity unavailable"
                        ) from None
                    await asyncio.sleep(0.1)
            store = get_store()
            if store is None or not all(
                app.state.components.get(name)
                for name in (
                    "storage",
                    "auth",
                    "rag",
                    "llm",
                )
            ):
                raise CoordinationUnavailableError(
                    "Immutable worker startup dependencies unavailable"
                )
            runtime = SharedQueryRuntime(
                coordinator,
                MySQLSessionStore(
                    store._pool,
                    settings.shared_worker_namespace,
                    settings.session_max_entries,
                    int(settings.session_ttl_seconds),
                ),
            )
            runtime.model_coordinator = MySQLCoordinator(
                pool,
                settings.shared_worker_namespace + ":model",
                settings.llm_max_concurrency,
            )
            await runtime.initialize()
            await runtime.model_coordinator.initialize()
            # Keep v1 fingerprints compatible with existing default namespaces.
            excluded_settings = set() if release.tenant_ids else {
                "tenant_admin_user_ids", "tenant_llm_endpoints", "tenant_query_profiles_path",
            }
            local_settings = settings.model_dump(exclude=excluded_settings)
            if _baseline_digest(await _persisted_state(store, release.tenant_ids)) != baseline:
                raise CoordinationUnavailableError("Configuration changed during worker startup")
            fingerprint = _hash(
                {
                    "release": release.digest,
                    "environment": local_settings,
                    "database_settings": baseline,
                }
            )
            async with asyncio.timeout(5), pool.acquire() as conn, conn.cursor() as cur:
                await cur.execute(
                    "CREATE TABLE IF NOT EXISTS worker_release_config ("
                    "namespace VARCHAR(24) PRIMARY KEY, fingerprint CHAR(64) NOT NULL)"
                )
                await cur.execute(
                    "INSERT IGNORE INTO worker_release_config VALUES (%s,%s)",
                    (coordinator.namespace, fingerprint),
                )
                await conn.commit()
                await cur.execute(
                    "SELECT fingerprint FROM worker_release_config WHERE namespace=%s",
                    (coordinator.namespace,),
                )
                if await cur.fetchone() != (fingerprint,):
                    raise CoordinationUnavailableError("Immutable worker configuration mismatch")

            async def verify() -> None:
                try:
                    async with asyncio.timeout(5):
                        await asyncio.to_thread(release.verify_metadata)
                        await asyncio.to_thread(release.catalog_for, settings.metric_catalog_path)
                        if any(dense.parent.glob("index-pending-*.json")):
                            raise ValueError("Interrupted index maintenance")
                        current_yaml = set((ROOT / "app/config").rglob("*.yml"))
                        current_yaml.update((ROOT / "app/workflow/plans").rglob("*.yml"))
                        if (
                            not {str(path.resolve()) for path in current_yaml}
                            <= release.files.keys()
                        ):
                            raise ValueError("Configuration inventory changed")
                        if _hash(settings.model_dump(exclude=excluded_settings)) != _hash(
                            local_settings
                        ):
                            raise ValueError("Process configuration changed")
                        if _baseline_digest(
                            await _persisted_state(store, release.tenant_ids)
                        ) != baseline:
                            raise ValueError("Persisted configuration changed")
                except Exception:
                    raise CoordinationUnavailableError(
                        "Immutable worker configuration changed"
                    ) from None

            runtime.verify_configuration = verify
            runtime.worker_release = release
            runtime.tenant_snapshots = tenant_snapshots
            if release.subagent_catalog is not None:
                from app.subagent.manager import SubagentManager

                registry = SubagentManager()
                for config in release.subagent_catalog.configs:
                    tenant_snapshot = tenant_snapshots.get(config.tenant_id)
                    if tenant_snapshot is not None and (
                        config.domain != "default"
                        and config.domain.casefold() not in tenant_snapshot.domains
                    ):
                        raise CoordinationUnavailableError("Sealed subagent domain is unavailable")
                    registry.register(config)
                runtime.subagents = registry
            publish_shared_runtime(runtime)
            app.state.components["shared_worker"] = True
            yield
        finally:
            if runtime is not None:
                runtime.ready = False
            publish_shared_runtime(None)
            # Also closes partially restored handles when initialization failed
            # before the ordinary lifespan could take ownership.
            await ConnectionFactory.aclose_all()
            pool.close()
            await pool.wait_closed()
            _settings_store.clear()
            _settings_store.update(prior_settings)
            for tenant in release.tenant_ids:
                _tenant_llm_runtimes.pop(tenant, None)


def mutable_request_allowed(method: str, path: str) -> bool:
    if not path.startswith("/api/v1/"):
        return True
    if method in ("GET", "HEAD", "OPTIONS"):
        return True
    if method != "POST":
        return False
    return path in {
        "/api/v1/auth/login",
        "/api/v1/query",
        "/api/v1/query/stream",
        "/api/v1/query/generate",
        "/api/v1/query/candidates",
        "/api/v1/query/explain",
        "/api/v1/execute",
        "/api/v1/sql/explain",
        "/api/v1/sql/translate",
        "/api/v1/sql/format",
        "/api/v1/sql/validate",
    } or bool(re.fullmatch(
        r"/api/v1/(?:sessions/[^/]+/versions/[0-9]+/(?:branch|rollback)|subagents/[^/]+/query)",
        path,
    ))
