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

import aiomysql
from fastapi import FastAPI

from app.config.settings import settings
from app.db.connections import ConnectionFactory, DatabaseType
from app.harness.mysql_coordination import (
    CoordinationBusyError,
    CoordinationUnavailableError,
    MySQLCoordinator,
)
from app.harness.shared_query_runtime import SharedQueryRuntime, publish_shared_runtime
from app.harness.worker_release import ROOT, WorkerRelease
from app.storage.mysql_store import MySQLStore, get_store
from app.storage.shared_sessions import MySQLSessionStore


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


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
    from app.api.settings import _settings_store

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
                        persisted = await MySQLStore(pool).get_settings()
                        baseline = _hash(persisted)
                        _settings_store.update(copy.deepcopy(persisted))
                        if ConnectionFactory.list_all():
                            raise CoordinationUnavailableError(
                                "Immutable worker requires a fresh registry"
                            )
                        for profile in release.profiles:
                            await ConnectionFactory.create(
                                DatabaseType(profile["db_type"]),
                                profile["config"],
                                profile["db_id"],
                            )
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
            local_settings = settings.model_dump()
            if _hash(await store.get_settings()) != baseline:
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
                        if any(dense.parent.glob("index-pending-*.json")):
                            raise ValueError("Interrupted index maintenance")
                        current_yaml = set((ROOT / "app/config").rglob("*.yml"))
                        current_yaml.update((ROOT / "app/workflow/plans").rglob("*.yml"))
                        if (
                            not {str(path.resolve()) for path in current_yaml}
                            <= release.files.keys()
                        ):
                            raise ValueError("Configuration inventory changed")
                        if _hash(settings.model_dump()) != _hash(local_settings):
                            raise ValueError("Process configuration changed")
                        if _hash(await store.get_settings()) != baseline:
                            raise ValueError("Persisted configuration changed")
                except Exception:
                    raise CoordinationUnavailableError(
                        "Immutable worker configuration changed"
                    ) from None

            runtime.verify_configuration = verify
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
    } or bool(re.fullmatch(r"/api/v1/sessions/[^/]+/versions/[0-9]+/(branch|rollback)", path))
