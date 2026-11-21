"""Run metric acceptance against FastAPI and independent SQLite reference queries."""

import asyncio
import hashlib
import socket
import sqlite3
from contextlib import closing, nullcontext

import httpx
import pytest
import uvicorn

from app.api import query
from app.config.settings import settings
from app.evaluation.artifacts import read_json, write_atomic
from app.evaluation.metric_acceptance import (
    PROFILE,
    load_metric_acceptance,
    verify_metric_acceptance,
)
from app.mcp.fastmcp_server import create_nl2sql_server
from scripts.run_metric_acceptance import main, target_origin
from tests.test_api.test_metric_query import metric_env as metric_env


@pytest.fixture
def acceptance_files(metric_env, tmp_path):
    _, _, catalog, database = metric_env
    with closing(sqlite3.connect(database)) as conn:
        grouped = conn.execute(
            "SELECT region, SUM(amount), COUNT(*) FROM orders GROUP BY region ORDER BY region"
        )
        expected_grouped = [list(row) for row in grouped]
        totals = conn.execute("SELECT SUM(amount) FROM orders WHERE status = ?", ("completed",))
        expected_filtered = [list(row) for row in totals]
    bundle = {
        "profile": PROFILE, "tenant_id": 1, "domain": "ecommerce", "database": "metric-db",
        "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
        "cases": [
            {"id": "grouped", "metrics": ["revenue", "order_count"], "dimensions": ["region"],
             "filters": {}, "expected_rows": expected_grouped},
            {"id": "filtered", "metrics": ["revenue"], "dimensions": [],
             "filters": {"status": "completed"}, "expected_rows": expected_filtered},
        ],
    }
    return catalog, tmp_path / "cases.json", bundle


async def _verify(metric_env, acceptance):
    fixture, *_ = metric_env
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fixture.app), base_url="http://127.0.0.1",
        headers={"Authorization": fixture.headers["Authorization"]},
    ) as client:
        return await verify_metric_acceptance(client, acceptance)


@pytest.mark.parametrize("mode", ["match", "result", "truncated", "catalog"])
async def test_release_verifies_real_results_and_drift(
    metric_env, acceptance_files, monkeypatch, mode,
):
    _, router, *_ = metric_env
    catalog, cases, bundle = acceptance_files
    if mode == "result":
        bundle["cases"][0]["expected_rows"][0][1] += 1
    write_atomic(cases, bundle)
    acceptance = load_metric_acceptance(catalog, cases)
    if mode == "catalog":
        # Same version but different raw catalog bytes.
        catalog.write_bytes(catalog.read_bytes() + b"\n")
    if mode == "truncated":
        monkeypatch.setattr(settings, "max_result_rows", 1)

    report = await _verify(metric_env, acceptance)
    assert router.calls == []
    assert report["catalog"]["sha256"] == bundle["catalog_sha256"]
    assert report["cases_sha256"] == hashlib.sha256(cases.read_bytes()).hexdigest()
    if mode == "catalog":
        assert not report["passed"]
        assert query._SESSION_STORE == {}
        assert report["checks"] == [{
            "check": "preflight", "passed": False, "error": "DISCOVERY_CATALOG_MISMATCH",
        }]
        return
    assert report["passed"] is (mode == "match")
    assert len(report["checks"]) == 6
    for check in report["checks"][1:3]:
        assert check["case"] == "grouped"
        if mode == "result":
            assert check["error"] == "ROW_MISMATCH"
        elif mode == "truncated":
            assert check["error"] == "RESULT_TRUNCATED"
        else:
            assert check["expected_sha256"] == check["observed_sha256"]
    assert {check["transport"] for check in report["checks"][1:3]} == {"json", "sse"}
    assert report["checks"][3]["passed"] and report["checks"][4]["passed"]


def test_cli_refuses_unapproved_catalog_change_and_keeps_report(
    acceptance_files, tmp_path, monkeypatch,
):
    catalog, cases, bundle = acceptance_files
    write_atomic(cases, bundle)
    catalog.write_bytes(catalog.read_bytes() + b"\n")
    report = tmp_path / "report.json"
    report.write_bytes(b"previous")
    monkeypatch.setenv("DE_METRIC_ACCEPTANCE_TOKEN", "test-token")
    assert main([
        "--catalog", str(catalog), "--cases", str(cases), "--output", str(report),
        "--base-url", "http://127.0.0.1:1",
    ]) == 2
    assert report.read_bytes() == b"previous"


@pytest.mark.parametrize("mode", ["rest", "mcp-json", "mcp-sse", "mcp-result", "mcp-unavailable"])
async def test_cli_checks_a_serving_http_endpoint(
    metric_env, acceptance_files, tmp_path, monkeypatch, mode,
):
    fixture, router, *_ = metric_env
    catalog, cases, bundle = acceptance_files
    if mode == "mcp-result":
        bundle["cases"][0]["expected_rows"][0][1] += 1
    write_atomic(cases, bundle)
    output = tmp_path / "report.json"
    token = fixture.headers["Authorization"][7:]
    monkeypatch.setenv("DE_METRIC_ACCEPTANCE_TOKEN", token)
    lifespan = nullcontext()
    if mode in {"mcp-json", "mcp-sse", "mcp-result"}:
        mcp_app = create_nl2sql_server().http_app(
            path="/", stateless_http=False, json_response=mode != "mcp-sse",
        )
        fixture.app.mount("/metric-mcp", mcp_app)
        lifespan = mcp_app.router.lifespan_context(mcp_app)
    server = uvicorn.Server(uvicorn.Config(fixture.app, lifespan="off", log_level="error"))
    async with lifespan:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
            mcp_url = origin + "/metric-mcp/"
            args = [
                "--catalog", str(catalog), "--cases", str(cases), "--output", str(output),
                "--base-url", origin,
            ]
            if mode != "rest":
                args.extend(["--mcp-url", mcp_url])
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                async with asyncio.timeout(10):
                    while not server.started:
                        await asyncio.sleep(0.01)
                status = await asyncio.to_thread(main, args)
            finally:
                server.should_exit = True
                await asyncio.wait_for(serving, 10)
    passed = mode in {"rest", "mcp-json", "mcp-sse"}
    assert status == (0 if passed else 1) and router.calls == []
    report = read_json(output)
    assert token not in output.read_text(encoding="utf-8")
    assert report["passed"] is passed and report["deployment_origin"] == origin
    if mode == "rest":
        assert "mcp_url" not in report and len(report["checks"]) == 6
        return
    assert report["mcp_url"] == mcp_url
    if mode == "mcp-unavailable":
        assert report["checks"][-1] == {
            "check": "mcp_preflight", "passed": False, "error": "MCP_TRANSPORT_FAILED",
        }
        return
    assert report["checks"][-4] == {"check": "mcp_preflight", "passed": True}
    assert report["checks"][-1] == {"check": "mcp_final_discovery", "passed": True}
    mcp_checks = [check for check in report["checks"] if check.get("transport") == "mcp"]
    assert [check["case"] for check in mcp_checks] == ["grouped", "filtered"]
    if mode == "mcp-result":
        assert mcp_checks[0]["error"] == "ROW_MISMATCH"
    else:
        assert mcp_checks[0]["expected_sha256"] == mcp_checks[0]["observed_sha256"]
    assert mcp_checks[1]["passed"]


@pytest.mark.parametrize("url", [
    "http://db.example", "https://user:pw@db.example", "https://db.example/query",
    "https://db.example/?token=example", "https://db.example/#fragment",
])
def test_cli_requires_an_origin_that_can_receive_the_token(url):
    with pytest.raises(ValueError):
        target_origin(url)
