"""Serve compiled frontend assets without falling back to runtime CDNs."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend_dist"


def register_frontend(app: FastAPI, directory: Path | None = None) -> None:
    """Keep API-only startup available even when the optional UI is not built."""
    root = directory if directory is not None else FRONTEND_DIR
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    else:
        @app.get("/assets/{filename:path}", include_in_schema=False)
        async def missing_asset(filename: str) -> PlainTextResponse:
            return PlainTextResponse("Not Found", status_code=404)

    def page(filename: str) -> FileResponse | JSONResponse:
        target = root / filename
        if not target.is_file() or not assets.is_dir() or not (root / "manifest.json").is_file():
            return JSONResponse(
                status_code=503,
                content={
                    "detail": {
                        "code": "FRONTEND_NOT_BUILT",
                        "message": "Frontend unavailable. Run npm ci --ignore-scripts "
                        "and npm run build:frontend before starting the service.",
                    }
                },
                headers={"Cache-Control": "no-store"},
            )
        return FileResponse(target, headers={"Cache-Control": "no-cache"})

    @app.get("/", include_in_schema=False, response_model=None)
    async def landing_page() -> FileResponse | JSONResponse:
        return page("landing_page.html")

    @app.get("/login", include_in_schema=False, response_model=None)
    async def login_page() -> FileResponse | JSONResponse:
        return page("form.html")

    @app.get("/workspace", include_in_schema=False, response_model=None)
    async def workspace_page() -> FileResponse | JSONResponse:
        return page("workspace.html")
