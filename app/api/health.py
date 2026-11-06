"""Health check endpoint."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Return system health status."""
    return {
        "status": "ok",
        "version": "0.1.0",
        "service": "NL2SQL Data Engineering Agent",
    }


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    """Execution readiness is separate from process liveness; never runs a fake probe."""
    from app.db.connections import ConnectionFactory
    from app.llm.factory import get_default_router

    components = dict(getattr(request.app.state, "components", {}))
    components["llm"] = not get_default_router().mock_mode
    from app.rag.engine import get_rag_engine

    try:
        schema_rag = get_rag_engine().schema_rag
        required = len(schema_rag.refresh_required_databases)
        failed = len(schema_rag.unavailable_databases)
        rag_status = "degraded" if required or failed else "ready"
    except RuntimeError:
        required, failed = 0, 0
        rag_status = "unavailable"
    components["rag"] = rag_status == "ready"
    database_count = len(ConnectionFactory.list_all())
    initialized = bool(getattr(request.app.state, "initialized", False))
    ready = (
        initialized and components.get("auth", False) and components["llm"] and database_count > 0
    )
    from app.config.settings import settings
    from app.harness.shared_query_runtime import current_shared_runtime

    if settings.shared_worker_release:
        shared = current_shared_runtime()
        shared_ready = shared is not None and shared.ready
        if shared_ready and shared is not None and shared.verify_configuration is not None:
            try:
                await shared.verify_configuration()
            except Exception:
                shared_ready = False
        components["shared_worker"] = shared_ready
        ready = ready and shared_ready and components.get("storage", False) and components["rag"]
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "initialized": initialized,
            "components": components,
            "connected_database_count": database_count,
            "database_probe": "registry only; no live database health query performed",
            "rag": {
                "status": rag_status,
                "refresh_required_database_count": required,
                "recovery_failed_database_count": failed,
                "probe": "schema index process state only; no live storage or model query",
            },
        },
    )
