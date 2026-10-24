"""FastAPI routes for queries, schemas, domains and workbench management."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """The sole owner of startup and awaited shutdown in all launch modes."""
    import asyncio

    from app.auth.database import close_auth_pool, init_auth_db
    from app.bootstrap import initialize_catalogs
    from app.db.connections import ConnectionFactory
    from app.llm.factory import get_default_router, reset_default_router
    from app.rag.engine import close_rag_engine, get_rag_engine, initialize_rag_engine
    from app.storage.mysql_store import get_store, init_db

    initialize_catalogs()
    components = {"storage": False, "auth": False, "rag": False, "llm": False}
    app.state.components = components
    app.state.initialized = False
    for name, initialize in [("storage", init_db), ("auth", init_auth_db)]:
        try:
            await asyncio.wait_for(initialize(), timeout=10)
            components[name] = True
        except Exception:
            logger.warning("Startup component unavailable: %s", name, exc_info=True)
    try:
        try:
            get_rag_engine()
        except RuntimeError:
            await asyncio.wait_for(initialize_rag_engine(), timeout=10)
        components["rag"] = True
    except Exception:
        close_rag_engine()
        logger.warning("RAG unavailable; schema linking will degrade explicitly", exc_info=True)
    try:
        from app.api.settings import reload_llm_router_from_db

        await asyncio.wait_for(reload_llm_router_from_db(), timeout=10)
        components["llm"] = not get_default_router().mock_mode
    except Exception:
        logger.warning("Persisted LLM settings unavailable", exc_info=True)
        components["llm"] = not get_default_router().mock_mode
    app.state.initialized = True
    try:
        yield
    finally:
        app.state.initialized = False
        from app.background import stop_background
        from app.subagent.manager import subagent_manager

        # Uvicorn drains requests before lifespan shutdown; close async handles explicitly.
        for name, close in [
            ("background", stop_background),
            ("subagents", subagent_manager.deactivate_all),
            ("databases", ConnectionFactory.aclose_all),
            ("auth", close_auth_pool),
        ]:
            try:
                await close()
            except Exception:
                logger.exception("Resource shutdown failed: %s", name)
        store = get_store()
        if store is not None:
            try:
                await store.close()
            except Exception:
                logger.exception("Storage shutdown failed")
        close_rag_engine()
        reset_default_router()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns a fully configured FastAPI app with all routers mounted.
    """
    from app.auth.access import require_operator
    from app.config.settings import settings

    if settings.jwt_secret == "change-me-in-production" or len(settings.jwt_secret) < 32:
        raise ValueError("JWT_SECRET must be configured with at least 32 characters")

    app = FastAPI(
        title="NL2SQL Data Engineering Agent",
        description="Natural Language to SQL — domain-aware, multi-LLM, multi-DB",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_lifespan,
    )

    # CORS — allow standalone HTML pages (opened from filesystem) to call the API
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    from app.api import (
        auth,
        documents,
        domains,
        feedback,
        health,
        learning,
        mcp_api,
        query,
        schema,
        settings,
        sql_utils,
        subagents,
        workflows,
    )

    app.include_router(health.router, tags=["Health"])
    protected = [Depends(require_operator)]
    app.include_router(query.router, dependencies=protected, prefix="/api/v1", tags=["Query"])
    app.include_router(schema.router, dependencies=protected, prefix="/api/v1", tags=["Schema"])
    app.include_router(domains.router, dependencies=protected, prefix="/api/v1", tags=["Domains"])
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
    app.include_router(
        workflows.router, dependencies=protected, prefix="/api/v1", tags=["Workflows"]
    )
    app.include_router(mcp_api.router, dependencies=protected, prefix="/api/v1", tags=["MCP"])
    app.include_router(feedback.router, dependencies=protected, prefix="/api/v1", tags=["Feedback"])
    app.include_router(learning.router, dependencies=protected, prefix="/api/v1", tags=["Learning"])
    app.include_router(
        documents.router, dependencies=protected, prefix="/api/v1", tags=["Documents"]
    )
    app.include_router(
        sql_utils.router, dependencies=protected, prefix="/api/v1", tags=["SQL Utils"]
    )
    app.include_router(settings.router, dependencies=protected, prefix="/api/v1", tags=["Settings"])
    app.include_router(
        subagents.router, dependencies=protected, prefix="/api/v1", tags=["Subagents"]
    )

    from pathlib import Path

    _data = Path(__file__).parent.parent.parent / "data"

    from app.api.frontend import register_frontend

    register_frontend(app)

    # Avatar static files → /avatars/{filename}
    @app.get("/avatars/{filename}", include_in_schema=False)
    async def avatar_file(filename: str):
        avatar_path = _data / "avatars" / filename
        if not avatar_path.exists():
            from fastapi.responses import PlainTextResponse

            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(str(avatar_path))

    return app
