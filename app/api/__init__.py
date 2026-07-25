"""REST API layer — FastAPI routes for query, schema, domains, auth, feedback, workflows, MCP, and SQL utilities."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Application lifespan — initialize MySQL storage on startup."""
    # Startup: init MySQL storage
    try:
        from app.storage.mysql_store import init_db
        await init_db()
        logger.info("MySQL storage initialized")
    except Exception:
        logger.warning("MySQL storage not available — using in-memory fallback")

    # Startup: init auth database
    try:
        from app.auth.database import init_auth_db
        await init_auth_db()
        logger.info("Auth database initialized")
    except Exception:
        logger.warning("Auth database not available — auth endpoints may not work")

    # Startup: init RAG engine (schema + metric + document hybrid search)
    try:
        from app.rag.engine import initialize_rag_engine, _engine
        if _engine is None:
            await initialize_rag_engine()
            logger.info("RAG engine initialized")
        else:
            logger.info("RAG engine already initialized (via main.py)")
    except Exception:
        logger.warning("RAG engine not available — hybrid search disabled")

    yield
    # Shutdown: close MySQL pool
    try:
        from app.storage.mysql_store import get_store
        store = get_store()
        if store is not None:
            await store.close()
            logger.info("MySQL storage closed")
    except Exception:
        pass

    # Shutdown: close auth pool
    try:
        from app.auth.database import close_auth_pool
        await close_auth_pool()
    except Exception:
        pass


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns a fully configured FastAPI app with all routers mounted.
    """
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
        allow_origins=["*"],
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
    app.include_router(query.router, prefix="/api/v1", tags=["Query"])
    app.include_router(schema.router, prefix="/api/v1", tags=["Schema"])
    app.include_router(domains.router, prefix="/api/v1", tags=["Domains"])
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
    app.include_router(workflows.router, prefix="/api/v1", tags=["Workflows"])
    app.include_router(mcp_api.router, prefix="/api/v1", tags=["MCP"])
    app.include_router(feedback.router, prefix="/api/v1", tags=["Feedback"])
    app.include_router(learning.router, prefix="/api/v1", tags=["Learning"])
    app.include_router(documents.router, prefix="/api/v1", tags=["Documents"])
    app.include_router(sql_utils.router, prefix="/api/v1", tags=["SQL Utils"])
    app.include_router(settings.router, prefix="/api/v1", tags=["Settings"])
    app.include_router(subagents.router, prefix="/api/v1", tags=["Subagents"])

    from pathlib import Path

    _frontend = Path(__file__).parent.parent.parent / "frontend_design"
    _data = Path(__file__).parent.parent.parent / "data"

    # Landing page → /
    @app.get("/", include_in_schema=False)
    async def landing_page():
        return FileResponse(str(_frontend / "landing_page.html"))

    # Login / Register → /login
    @app.get("/login", include_in_schema=False)
    async def login_page():
        return FileResponse(str(_frontend / "form.html"))

    # Workspace → /workspace
    @app.get("/workspace", include_in_schema=False)
    async def workspace_page():
        return FileResponse(str(_frontend / "workspace.html"))

    # Avatar static files → /avatars/{filename}
    @app.get("/avatars/{filename}", include_in_schema=False)
    async def avatar_file(filename: str):
        avatar_path = _data / "avatars" / filename
        if not avatar_path.exists():
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse("Not Found", status_code=404)
        return FileResponse(str(avatar_path))

    return app
