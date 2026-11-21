"""HTTP capacity checks using real query execution and controlled transport faults."""

import asyncio
import hashlib
import json
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.audit.store import usage_snapshot
from app.config.settings import settings
from app.evaluation.artifacts import read_json, write_atomic
from app.evaluation.capacity_acceptance import (
    MULTI_TENANT_PROFILE,
    PROFILE,
    load_capacity_plan,
    run_capacity_plan,
)
from scripts.run_capacity_acceptance import main
from tests.test_api.test_plan_execution import ScriptedRouter
from tests.test_api.test_plan_execution import plan_env as plan_env


def _plan():
    return {
        "profile": PROFILE,
        "tenant_id": 1,
        "workload": [
            {"id": protocol, "transport": protocol, "database": "plan", "domain": "ecommerce",
             "nl_text": "list all order ids"}
            for protocol in ("json", "sse")
        ],
        "phases": [
            {"id": name, "requests": 4, "concurrency": concurrency, "timeout_seconds": 5,
             "slo": {"min_success_rate": 1, "max_success_p95_ms": 5000,
                     "max_sse_first_event_p95_ms": first_event}}
            for name, concurrency, first_event in (
                ("steady", 1, 5000), ("peak", 4, None), ("recovery", 1, 5000),
            )
        ],
    }


@asynccontextmanager
async def _serve(app):
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    await asyncio.sleep(0.01)
            yield origin
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, 10)


async def test_cli_keeps_backpressure_in_denominator_and_measures_recovery(
    plan_env, tmp_path, monkeypatch,
):
    class HeldRouter(ScriptedRouter):
        async def complete(self, messages, **kwargs):
            await asyncio.sleep(0.16)
            return await super().complete(messages, **kwargs)

    router = HeldRouter(["SELECT id FROM orders ORDER BY id"])
    fixture = plan_env(router)
    monkeypatch.setattr(settings, "max_concurrent_queries", 1)
    token = fixture.headers["Authorization"][7:]
    monkeypatch.setenv("DE_CAPACITY_ACCEPTANCE_TOKEN", token)
    monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path / "traces"))
    manifest, output = tmp_path / "manifest.json", tmp_path / "report.json"
    write_atomic(manifest, _plan())
    async with _serve(fixture.app) as origin:
        # Warm the local code and retrieval caches before the steady-state phase.
        async with httpx.AsyncClient(base_url=origin, timeout=30, trust_env=False) as client:
            warmup = await client.post(
                "/api/v1/query", json=load_capacity_plan(manifest).workload[0].body,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert warmup.status_code == 200 and warmup.json()["status"] == "completed"
        status = await asyncio.to_thread(main, [
            "--base-url", origin, "--manifest", str(manifest), "--output", str(output),
        ])
    assert status == 1
    report = read_json(output)
    assert report["passed"] is False and report["actual_cost_usd"] is None
    assert report["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert token not in output.read_text(encoding="utf-8")
    steady, peak, recovery = report["phases"]
    for phase in (steady, peak, recovery):
        started = datetime.fromisoformat(phase["started_at_utc"])
        finished = datetime.fromisoformat(phase["finished_at_utc"])
        assert started.tzinfo is UTC and finished.tzinfo is UTC and finished >= started
    assert steady["passed"] and recovery["passed"]
    assert steady["sse_first_event_latency"]["success"]["p95_ms"] is not None
    assert steady["by_transport"]["sse"]["successful"] == 2
    assert peak["latency"]["attempts"] == 4
    failures = peak["failure_counts"]
    assert failures.get("HTTP_503", 0) + failures.get("SSE_QUERY_CAPACITY", 0) > 0
    assert peak["successful"] + sum(failures.values()) == 4
    assert not peak["slo_checks"]["min_success_rate"]
    assert recovery["success_rate"] == 1.0


async def test_incomplete_sse_and_client_timeout_are_kept_in_report(tmp_path):
    app = FastAPI()

    @app.get("/api/v1/auth/workspace")
    async def workspace():
        return {"tenant_id": 1, "access": "system"}

    @app.post("/api/v1/query")
    async def timeout():
        await asyncio.sleep(1)
        return {}

    @app.post("/api/v1/query/stream")
    async def incomplete():
        async def frames():
            yield b"event: workflow_start\ndata: {}\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream")

    plan = _plan()
    plan["phases"] = [plan["phases"][0]]
    plan["phases"][0]["timeout_seconds"] = 0.1
    manifest = tmp_path / "manifest.json"
    write_atomic(manifest, plan)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
    ) as client:
        report = await run_capacity_plan(client, load_capacity_plan(manifest))
        plan["tenant_id"] = 2
        write_atomic(manifest, plan)
        denied = await run_capacity_plan(client, load_capacity_plan(manifest))
    phase = report["phases"][0]
    assert phase["failure_counts"] == {"CLIENT_TIMEOUT": 2, "SSE_INCOMPLETE": 2}
    assert phase["sse_first_event_latency"]["attempts"] == 2
    assert phase["sse_first_event_latency"]["successful"] == 0
    assert denied["preflight_error"] == "TENANT_MISMATCH" and denied["phases"] == []


def test_invalid_manifest_does_not_replace_report(tmp_path):
    plan = _plan()
    plan["phases"][0]["concurrency"] = 33
    manifest, output = tmp_path / "manifest.json", tmp_path / "report.json"
    write_atomic(manifest, plan)
    output.write_bytes(b"previous")
    assert main([
        "--base-url", "http://127.0.0.1:1", "--manifest", str(manifest), "--output", str(output),
    ]) == 2
    assert output.read_bytes() == b"previous"


async def test_extended_slos_require_measured_throughput_latency_and_complete_tokens(tmp_path):
    app = FastAPI()
    complete_usage = usage_snapshot({"attempts": [{
        "status": "completed", "prompt_tokens": 3, "completion_tokens": 5,
    }], "dropped_attempts": 0})
    empty_usage = usage_snapshot({"dropped_attempts": 0})
    json_calls, sse_usage = 1, complete_usage

    @app.get("/api/v1/auth/workspace")
    async def workspace():
        return {"tenant_id": 1, "access": "system"}

    def completed(calls, usage):
        return {
            "status": "completed", "plan_id": "request_query",
            "validation": {"passed": True},
            "execution": {"rows": [[1]], "row_count": 1, "truncated": False},
            "llm_calls": calls, "llm_usage": usage,
        }

    @app.post("/api/v1/query")
    async def single():
        await asyncio.sleep(0.02)
        return completed(json_calls, complete_usage if json_calls else empty_usage)

    @app.post("/api/v1/query/stream")
    async def stream():
        frame = f"event: done\ndata: {json.dumps(completed(1, sse_usage))}\n\n"
        return StreamingResponse(iter([frame]), media_type="text/event-stream")

    strict = _plan()
    passing, failing, _ = strict["phases"]
    strict["phases"] = [passing, failing]
    gates = {
        "max_success_p99_ms": 5000, "min_successful_rps": 0.001, "max_total_llm_tokens": 32,
    }
    passing["slo"].update(gates)
    failing["slo"].update(
        max_success_p99_ms=1, min_successful_rps=1_000_000, max_total_llm_tokens=31,
    )
    manifest = tmp_path / "extended.json"
    write_atomic(manifest, strict)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
    ) as client:
        report = await run_capacity_plan(client, load_capacity_plan(manifest))
        passed, failed = report["phases"]
        assert passed["passed"] and not failed["passed"] and not report["passed"]
        assert all(passed["slo_checks"][key] for key in gates)
        assert not any(failed["slo_checks"][key] for key in gates)
        assert passed["llm_usage"]["total_llm_tokens"] == 32

        strict["phases"] = [passing]
        json_calls = 0
        sse_usage = empty_usage  # An empty ledger after a recorded call is missing token evidence.
        write_atomic(manifest, strict)
        report = await run_capacity_plan(client, load_capacity_plan(manifest))
        phase = report["phases"][0]
        assert phase["successful"] == 4 and not phase["slo_checks"]["max_total_llm_tokens"]
        assert phase["llm_usage"]["total_llm_tokens"] is None
        assert phase["llm_usage"]["requests_with_accounted_tokens"] == 2
        assert phase["by_transport"]["json"]["llm_usage"]["total_llm_tokens"] == 0


def _multi_plan():
    plan = _plan()
    plan.pop("tenant_id")
    plan.update(profile=MULTI_TENANT_PROFILE, tenant_ids=[2, 3])
    for tenant, item in zip(plan["tenant_ids"], plan["workload"], strict=True):
        item.update(tenant_id=tenant, transport="json")
    peak = plan["phases"][1]
    peak.update(requests=2, concurrency=2)
    peak["slo"].update(
        min_success_rate=0.5, min_success_rate_by_tenant={"2": 1, "3": 1},
        max_success_p99_ms=5000, min_successful_rps=0.001,
    )
    plan["phases"] = [peak]
    return plan


async def test_multi_tenant_cli_requires_all_identity_checks_and_detects_starvation(
    tmp_path, monkeypatch,
):
    app = FastAPI()
    tokens = {"Bearer probe-fixture-two": 2, "Bearer probe-fixture-three": 3}
    entered = []
    both_running = asyncio.Event()

    @app.get("/api/v1/auth/workspace")
    async def workspace(request: Request):
        return {"tenant_id": tokens[request.headers["Authorization"]], "access": "tenant"}

    @app.post("/api/v1/query")
    async def measured(request: Request):
        body = await request.json()
        assert body["database"] == "plan" and "tenant_id" not in body
        tenant = tokens[request.headers["Authorization"]]
        entered.append(tenant)
        if len(entered) == 2:
            both_running.set()
        async with asyncio.timeout(2):
            await both_running.wait()
        if tenant == 3:
            raise HTTPException(503)
        return {
            "status": "completed", "plan_id": "request_query",
            "validation": {"passed": True},
            "execution": {"rows": [[200]], "row_count": 1, "truncated": False},
        }

    manifest, output = tmp_path / "manifest.json", tmp_path / "report.json"
    write_atomic(manifest, _multi_plan())
    monkeypatch.setenv("DE_CAPACITY_ACCEPTANCE_TOKEN_2", "probe-fixture-two")
    monkeypatch.setenv("DE_CAPACITY_ACCEPTANCE_TOKEN_3", "probe-fixture-three")
    async with _serve(app) as origin:
        args = ["--base-url", origin, "--manifest", str(manifest), "--output", str(output)]
        assert await asyncio.to_thread(main, args) == 1
        report = read_json(output)
        assert sorted(entered) == [2, 3] and not report["passed"]
        phase = report["phases"][0]
        assert phase["slo_checks"]["min_success_rate"] is True
        assert phase["slo_checks"]["max_success_p99_ms"] is True
        assert phase["slo_checks"]["min_successful_rps"] is True
        assert phase["tenant_slo_checks"] == {"2": True, "3": False}
        assert not phase["passed"] and phase["by_tenant"]["2"]["successful"] == 1
        assert phase["by_tenant"]["3"]["failure_counts"] == {"HTTP_503": 1}

        monkeypatch.setenv("DE_CAPACITY_ACCEPTANCE_TOKEN_3", "probe-fixture-two")
        assert await asyncio.to_thread(main, args) == 1
        denied = read_json(output)
        assert denied["preflight_error"] == "TENANT_PREFLIGHT_FAILED" and denied["phases"] == []
        assert denied["tenant_preflight"]["3"]["error"] == "TENANT_MISMATCH"
        assert sorted(entered) == [2, 3]
        assert "probe-fixture" not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("field", [
    "duplicate_tenant", "unknown_case_tenant", "unknown_slo_tenant", "invalid_token_limit",
])
def test_multi_tenant_manifest_checks_actor_and_threshold_coverage(tmp_path, field):
    manifest = tmp_path / "manifest.json"
    plan = _multi_plan()
    if field == "duplicate_tenant":
        plan["tenant_ids"] = [2, 2]
    elif field == "unknown_case_tenant":
        plan["workload"][0]["tenant_id"] = 4
    elif field == "unknown_slo_tenant":
        plan["phases"][0]["slo"]["min_success_rate_by_tenant"]["4"] = 1
    else:
        plan["phases"][0]["slo"]["max_total_llm_tokens"] = True
    write_atomic(manifest, plan)
    with pytest.raises(ValueError):
        load_capacity_plan(manifest)
