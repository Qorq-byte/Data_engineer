"""API deadline/backpressure contracts and per-session request sequencing."""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.api import create_app, query
from app.config.settings import settings
from app.workflow import query_service
from tests.api_client import authenticated_client


async def test_same_session_is_serial_and_other_session_is_independent(monkeypatch):
    active = set()
    first_entered = asyncio.Event()
    release = asyncio.Event()
    entered = []

    async def run(body, *args, **kwargs):
        assert body.session_id not in active
        active.add(body.session_id)
        entered.append(body.session_id)
        if body.nl_text == "first":
            first_entered.set()
            await release.wait()
        active.remove(body.session_id)
        return {}

    monkeypatch.setattr(query_service, "_run_query", run)
    first = asyncio.create_task(
        query_service.run_query(query.QueryRequest(nl_text="first", session_id="same"))
    )
    await first_entered.wait()
    second = asyncio.create_task(
        query_service.run_query(query.QueryRequest(nl_text="second", session_id="same"))
    )
    await query_service.run_query(query.QueryRequest(nl_text="other", session_id="other"))
    assert entered == ["same", "other"]
    release.set()
    await asyncio.gather(first, second)
    assert entered == ["same", "other", "same"]


async def test_capacity_rejects_and_slot_recovers_after_cancel(monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_queries", 1)
    entered = asyncio.Event()

    async def wait(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(query_service, "_run_query", wait)
    task = asyncio.create_task(query_service.run_query(query.QueryRequest(nl_text="wait")))
    await entered.wait()
    with pytest.raises(query_service.QueryCapacityError):
        await query_service.run_query(query.QueryRequest(nl_text="reject"))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async def complete(*args, **kwargs):
        return {"status": "completed"}

    monkeypatch.setattr(query_service, "_run_query", complete)
    assert (await query_service.run_query(query.QueryRequest(nl_text="recover")))[
        "status"
    ] == "completed"


def test_json_and_sse_deadline_report_errors_without_retry(monkeypatch):
    monkeypatch.setattr(settings, "query_timeout_seconds", 0.01)
    attempts = []

    async def wait(*args, **kwargs):
        attempts.append(True)
        await asyncio.Event().wait()

    monkeypatch.setattr(query_service, "run_query", wait)
    client = authenticated_client(create_app())
    response = client.post("/api/v1/query", json={"nl_text": "wait"})
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "QUERY_TIMEOUT"
    stream = client.post("/api/v1/query/stream", json={"nl_text": "wait"})
    assert "QUERY_TIMEOUT" in stream.text
    assert "event: done" not in stream.text
    assert len(attempts) == 2


async def test_session_capacity_and_expiry(monkeypatch):
    monkeypatch.setattr(query, "_SESSION_STORE", {})
    monkeypatch.setattr(settings, "session_max_entries", 2)
    first = query._get_or_create_session("expired")
    first["updated_at"] = (datetime.now() - timedelta(hours=2)).isoformat()
    query._get_or_create_session("live")
    query._get_or_create_session("new")
    assert "expired" not in query._SESSION_STORE
    assert len(query._SESSION_STORE) == 2
    query._get_or_create_session("overflow")
    assert len(query._SESSION_STORE) == 2


def test_liveness_does_not_claim_execution_readiness():
    client = authenticated_client(create_app())
    assert client.get("/health").status_code == 200
    result = client.get("/health/ready")
    assert result.status_code == 503
    assert result.json()["status"] == "not_ready"


def test_version_growth_is_rejected_without_losing_originals(monkeypatch):
    monkeypatch.setattr(settings, "session_max_versions", 1)
    monkeypatch.setattr(query, "_SESSION_STORE", {"capped": {"turns": [{"sql": "SELECT 1"}]}})
    client = authenticated_client(create_app())
    response = client.post(
        "/api/v1/sessions/capped/versions/1/branch", json={"edited_sql": "SELECT 2"}
    )
    assert response.status_code == 409
    assert query._SESSION_STORE["capped"]["versions"][0]["sql_text"] == "SELECT 1"
