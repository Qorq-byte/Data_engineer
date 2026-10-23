"""Verify compiled UI delivery and API-only startup without public source access."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import create_app
from app.api.frontend import register_frontend


@pytest.mark.parametrize("route", ["/", "/login", "/workspace"])
def test_missing_frontend_is_explicit_and_does_not_require_cdn(tmp_path, route):
    app = FastAPI()
    register_frontend(app, tmp_path / "missing")
    client = TestClient(app)
    response = client.get(route)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "FRONTEND_NOT_BUILT"
    assert "npm run build:frontend" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/assets/missing.js").status_code == 404


def test_compiled_pages_assets_and_source_boundaries(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app-123.js").write_text("console.log('local')", encoding="utf-8")
    (assets / "style-123.css").write_text("body{margin:0}", encoding="utf-8")
    (tmp_path / "private.env").write_text("synthetic private fixture", encoding="utf-8")
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    for filename in ("landing_page.html", "form.html", "workspace.html"):
        (tmp_path / filename).write_text(
            "<!doctype html><title>local build</title>", encoding="utf-8"
        )
    app = FastAPI()
    register_frontend(app, tmp_path)
    client = TestClient(app)
    for route in ("/", "/login", "/workspace"):
        response = client.get(route)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-cache"
    javascript = client.get("/assets/app-123.js")
    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert client.get("/assets/style-123.css").headers["content-type"].startswith("text/css")
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/assets/%2e%2e/private.env").status_code == 404
    assert client.get("/frontend_design/workspace.html").status_code == 404
    assert client.get("/node_modules/react/package.json").status_code == 404


def test_incomplete_build_never_serves_a_successful_page(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "workspace.html").write_text("partial build", encoding="utf-8")
    app = FastAPI()
    register_frontend(app, tmp_path)
    assert TestClient(app).get("/workspace").status_code == 503


def test_real_app_mounts_frontend_but_preserves_api_when_unbuilt(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.frontend.FRONTEND_DIR", tmp_path)
    client = TestClient(create_app())
    assert client.get("/").status_code == 503
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/databases").status_code == 401
