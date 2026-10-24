"""Real file persistence and real request/protocol integration; no external services."""

import asyncio
import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api import create_app, query
from app.config.settings import settings
from app.llm.usage import UsageCollector, observe
from app.workflow import query_service
from tests.api_client import authenticated_client
from tests.test_api import test_query_contract as contracts
from tests.test_llm.test_sdk_boundary import native_router

connected_query = contracts.connected_query


def sample(query_id="q_000000000001", outcome="completed"):
    from app.audit.store import UsageAuditRecord

    c = UsageCollector()
    with observe(c, "openai", "fixture", "transport_boundary"):
        c.observe(
            {"usage": {"prompt_tokens": 0, "completion_tokens": 2}}, usage_basis="sdk_normalized"
        )
    return UsageAuditRecord(
        query_id=query_id,
        session_id="private session",
        outcome=outcome,
        llm_calls=1,
        usage=c.summary(),
    )


def test_file_round_trip_and_fresh_process(tmp_path):
    from app.audit.store import UsageAuditStore

    path = tmp_path / "audit.sqlite3"
    s = UsageAuditStore(str(path), 10)
    record = sample()
    s.write(record)
    result = UsageAuditStore(str(path), 10).read(record.query_id)
    assert result["usage"]["attempts"][0]["prompt_tokens"] == 0
    assert result["usage"]["attempts"][0]["usage_basis"] == "sdk_normalized"
    assert result["session_ref"] != record.session_id
    assert "private session" not in str(result)
    code = (
        "from app.audit.store import UsageAuditStore; import json,sys; "
        "print(json.dumps(UsageAuditStore(sys.argv[1],10).read(sys.argv[2])))"
    )
    run = subprocess.run(
        [sys.executable, "-c", code, str(path), record.query_id],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert json.loads(run.stdout) == result


def test_allowlist_and_unknowns_survive(tmp_path):
    from app.audit.store import UsageAuditStore

    record = sample()
    record.usage["prompt"] = "private prompt"
    record.usage["attempts"][0].update(
        provider="https://private.example/?key=secret",
        model="SELECT private_data",
        prompt="private prompt",
    )
    record.usage["attempts"][0]["prompt_tokens"] = None
    record.usage["known_prompt_tokens"] = 0
    s = UsageAuditStore(str(tmp_path / "audit.db"), 10)
    s.write(record)
    r = s.read(record.query_id)
    assert r["usage"]["attempts"][0]["prompt_tokens"] is None
    assert r["usage"]["attempts"][0]["provider"] == "redacted"
    assert r["usage"]["attempts"][0]["model"] == "redacted"
    assert "private" not in str(r)
    assert "secret" not in str(r)


def test_idempotency_conflict_and_retention(tmp_path):
    from app.audit.store import UsageAuditStore

    s = UsageAuditStore(str(tmp_path / "audit.db"), 2)
    r = sample()
    s.write(r)
    s.write(r)
    with pytest.raises(ValueError):
        s.write(replace(r, outcome="failed"))
    assert s.read(r.query_id)["outcome"] == "completed"
    s.write(sample("q_000000000002"))
    s.write(sample("q_000000000003"))
    assert s.read(r.query_id) is None
    with sqlite3.connect(s.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM llm_usage_audit").fetchone()[0] == 2


def test_concurrent_writers_do_not_mix_records(tmp_path):
    from app.audit.store import UsageAuditStore

    s = UsageAuditStore(str(tmp_path / "audit.db"), 20)
    records = [sample(f"q_{i:012x}") for i in range(8)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(s.write, records))
    assert [s.read(r.query_id)["query_id"] for r in records] == [r.query_id for r in records]


@pytest.mark.parametrize("kind", ["foreign", "version", "corrupt"])
def test_foreign_or_unknown_schema_is_not_modified(tmp_path, kind):
    from app.audit.store import UsageAuditStore

    path = tmp_path / "bad.db"
    if kind == "corrupt":
        path.write_bytes(b"not a database")
    else:
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE foreign_data(x)")
            if kind == "version":
                conn.execute("PRAGMA user_version=99")
    before = path.read_bytes()
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        UsageAuditStore(str(path), 10).write(sample())
    assert path.read_bytes() == before


def test_read_missing_does_not_create_file(tmp_path):
    from app.audit.store import UsageAuditStore

    path = tmp_path / "absent" / "audit.db"
    assert UsageAuditStore(str(path), 10).read("q_000000000001") is None
    assert not path.parent.exists()


def test_locked_file_fails_without_record(tmp_path):
    from app.audit.store import UsageAuditStore

    s = UsageAuditStore(str(tmp_path / "audit.db"), 10)
    s.write(sample())
    with sqlite3.connect(s.path) as conn:
        conn.execute("BEGIN EXCLUSIVE")
        with pytest.raises(sqlite3.OperationalError):
            s.write(sample("q_000000000002"))
        conn.rollback()
    assert s.read("q_000000000002") is None


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    path = tmp_path / "usage.db"
    monkeypatch.setattr(settings, "llm_audit_path", str(path))
    return path


@pytest.mark.parametrize("protocol", ["json", "sse", "mcp"])
@pytest.mark.parametrize("reason", ["stop", "length"])
async def test_protocol_audit_matches_persisted_summary(
    connected_query, audit_path, monkeypatch, protocol, reason
):
    import litellm

    async def transport(**options):
        return litellm.ModelResponse(
            model="fixture",
            usage={"prompt_tokens": 4, "completion_tokens": 2},
            choices=[
                {
                    "finish_reason": reason,
                    "message": {
                        "role": "assistant",
                        "content": "SELECT id FROM orders ORDER BY id",
                    },
                }
            ],
        )

    monkeypatch.setattr(litellm, "acompletion", transport)
    monkeypatch.setattr(query, "get_default_router", lambda: native_router(max_retries=0))
    body = {"nl_text": "find orders", "database": "contract", "execute": True, "num_candidates": 1}
    if protocol == "mcp":
        response = await contracts.mcp_query(nl_text="find orders", db_id="contract")
    elif protocol == "sse":
        response = next(
            v
            for e, v in contracts.stream_events(
                connected_query.post("/api/v1/query/stream", json=body)
            )
            if e == "done"
        )
    else:
        response = connected_query.post("/api/v1/query", json=body).json()
    assert response["llm_audit"]["status"] == "persisted"
    stored = connected_query.get(f"/api/v1/usage-audits/{response['query_id']}")
    assert stored.status_code == 200
    r = stored.json()
    assert r["usage"] == response["llm_usage"]
    assert r["llm_calls"] == response["llm_calls"]
    assert r["outcome"] == response["status"]
    assert (r["outcome"] == "completed") == (reason == "stop")
    assert "SELECT" not in str(r)
    assert "find orders" not in str(r)


async def test_cancellation_audits_after_provider_cleanup(audit_path):
    from app.audit.store import UsageAuditStore

    entered = asyncio.Event()
    closed = []
    events = []
    r = native_router(max_retries=0)

    async def call(*args, **kwargs):
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    r._call_provider = call

    async def emit(event, payload):
        events.append((event, payload))

    task = asyncio.create_task(
        query_service.run_query(
            query.QueryRequest(
                nl_text="private text",
                link_schema=False,
                run_validation=False,
                num_candidates=1,
            ),
            emit,
            llm_router=r,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed
    query_id = next(p["query_id"] for e, p in events if e == "workflow_start")
    record = UsageAuditStore(str(audit_path), 10).read(query_id)
    assert record["outcome"] == "cancelled"
    assert record["llm_calls"] == 1
    assert record["usage"]["attempts"][0]["status"] == "cancelled"
    assert "private text" not in str(record)


async def test_infrastructure_error_is_saved_without_body(audit_path, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("private failure")

    monkeypatch.setattr(query_service, "_run_query", fail)
    with pytest.raises(RuntimeError, match="private failure"):
        await query_service.run_query(query.QueryRequest(nl_text="private input"))
    with sqlite3.connect(audit_path) as conn:
        payload = conn.execute("SELECT payload FROM llm_usage_audit").fetchone()[0]
    record = json.loads(payload)
    assert record["outcome"] == "error"
    assert record["usage"]["attempt_count"] == 0
    assert "private" not in payload


async def test_write_failure_does_not_change_workflow_result(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "llm_audit_path", str(tmp_path))

    async def done(*args, **kwargs):
        return {"status": "completed", "warnings": []}

    monkeypatch.setattr(query_service, "_run_query", done)
    response = await query_service.run_query(query.QueryRequest(nl_text="test"))
    assert response["status"] == "completed"
    assert response["llm_audit"]["status"] == "failed"
    assert str(tmp_path) not in str(response)


async def test_disabled_audit_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "llm_audit_path", "")

    async def done(*args, **kwargs):
        return {"status": "completed"}

    monkeypatch.setattr(query_service, "_run_query", done)
    response = await query_service.run_query(query.QueryRequest(nl_text="test"))
    assert response["llm_audit"]["status"] == "disabled"


def test_read_endpoint_auth_and_errors(audit_path, monkeypatch):
    app = create_app()
    client = authenticated_client(app)
    from app.auth.security import create_access_token

    other = TestClient(app, headers={"Authorization": f"Bearer {create_access_token(2, 'other')}"})
    assert other.get("/api/v1/usage-audits/q_000000000001").status_code == 403
    assert TestClient(app).get("/api/v1/usage-audits/q_000000000001").status_code == 401
    assert client.get("/api/v1/usage-audits/bad-id").status_code == 422
    assert client.get("/api/v1/usage-audits/q_000000000001").status_code == 404
    assert not audit_path.exists()
    audit_path.write_bytes(b"bad database")
    response = client.get("/api/v1/usage-audits/q_000000000001")
    assert response.status_code == 503
    assert str(audit_path) not in response.text
    monkeypatch.setattr(settings, "llm_audit_path", "")
    assert (
        client.get("/api/v1/usage-audits/q_000000000001").json()["detail"]["code"]
        == "USAGE_AUDIT_DISABLED"
    )


async def test_thread_cleanup_survives_repeated_cancellation():
    from app.audit.store import audit_io

    entered = threading.Event()
    release = threading.Event()
    closed = []

    def operation():
        entered.set()
        release.wait(5)
        closed.append(True)

    task = asyncio.create_task(audit_io(operation))
    await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed


@pytest.mark.parametrize("protocol", ["json", "sse", "mcp"])
@pytest.mark.parametrize("persistence", ["disabled", "failed"])
async def test_protocol_persistence_status_is_truthful(
    connected_query, tmp_path, monkeypatch, protocol, persistence
):
    monkeypatch.setattr(
        settings, "llm_audit_path", "" if persistence == "disabled" else str(tmp_path)
    )
    body = {"nl_text": "find orders", "database": "contract", "execute": True, "num_candidates": 1}
    if protocol == "mcp":
        response = await contracts.mcp_query(nl_text="find orders", db_id="contract")
    elif protocol == "sse":
        response = next(
            v
            for e, v in contracts.stream_events(
                connected_query.post("/api/v1/query/stream", json=body)
            )
            if e == "done"
        )
    else:
        response = connected_query.post("/api/v1/query", json=body).json()
    assert response["status"] == "completed"
    assert response["llm_audit"]["status"] == persistence
    assert response["llm_audit"]["query_id"] == response["query_id"]
    assert str(tmp_path) not in str(response)


async def test_concurrent_request_audits_keep_their_own_tokens(audit_path):
    from app.audit.store import UsageAuditStore

    r = native_router(max_retries=0)

    async def call(provider, model, messages, *args, **kwargs):
        text = str(messages)
        count = 10 if "request_alpha" in text else 20
        await asyncio.sleep(0)
        return {
            "choices": [{"finish_reason": "stop", "message": {"content": "SELECT 1"}}],
            "usage": {"prompt_tokens": count, "completion_tokens": 1},
        }

    r._call_provider = call

    async def request(name):
        return await query_service.run_query(
            query.QueryRequest(
                nl_text=name,
                session_id=name,
                link_schema=False,
                run_validation=False,
                num_candidates=1,
            ),
            llm_router=r,
        )

    a, b = await asyncio.gather(request("request_alpha"), request("request_beta"))
    s = UsageAuditStore(str(audit_path), 10)
    assert s.read(a["query_id"])["usage"]["known_prompt_tokens"] == 10
    assert s.read(b["query_id"])["usage"]["known_prompt_tokens"] == 20
    assert a["query_id"] != b["query_id"]


async def test_capacity_rejection_has_no_extra_record(audit_path, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_queries", 1)
    entered = asyncio.Event()

    async def wait(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(query_service, "_run_query", wait)
    task = asyncio.create_task(query_service.run_query(query.QueryRequest(nl_text="first")))
    await entered.wait()
    with pytest.raises(query_service.QueryCapacityError):
        await query_service.run_query(query.QueryRequest(nl_text="reject"))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with sqlite3.connect(audit_path) as conn:
        rows = conn.execute("SELECT payload FROM llm_usage_audit").fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0])["outcome"] == "cancelled"


@pytest.mark.parametrize("bad", [0, -1, True, 10001])
def test_retention_configuration_rejected(bad, tmp_path):
    from app.audit.store import UsageAuditStore

    with pytest.raises(ValueError):
        UsageAuditStore(str(tmp_path / "audit.db"), bad)


def test_oversize_snapshot_does_not_create_file(tmp_path):
    from app.audit.store import UsageAuditStore

    record = sample()
    record.usage["attempts"] *= 1001
    path = tmp_path / "audit.db"
    with pytest.raises(ValueError):
        UsageAuditStore(str(path), 10).write(record)
    assert not path.exists()


def test_audit_import_does_not_load_mysql_or_llm_sdk():
    code = """
import importlib.abc
import sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in ("aiomysql", "litellm"):
            raise AssertionError("unexpected driver import")
sys.meta_path.insert(0, Guard())
from app.audit.store import UsageAuditStore
assert "aiomysql" not in sys.modules
assert "litellm" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=20)


async def test_session_queue_cancellation_has_no_audit(audit_path, monkeypatch):
    entered = asyncio.Event()

    async def wait(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(query_service, "_run_query", wait)
    body = query.QueryRequest(nl_text="first", session_id="shared")
    first = asyncio.create_task(query_service.run_query(body))
    await entered.wait()
    queued = asyncio.create_task(
        query_service.run_query(query.QueryRequest(nl_text="second", session_id="shared"))
    )
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert not audit_path.exists()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    with sqlite3.connect(audit_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM llm_usage_audit").fetchone()[0] == 1


def test_retention_failure_rolls_back_new_row_and_preserves_previous(tmp_path):
    from app.audit.store import UsageAuditStore

    s = UsageAuditStore(str(tmp_path / "audit.db"), 2)
    s.write(sample("q_000000000001"))
    s.write(sample("q_000000000002"))
    with sqlite3.connect(s.path) as conn:
        conn.execute(
            "CREATE TRIGGER deny_delete BEFORE DELETE ON llm_usage_audit "
            "BEGIN SELECT RAISE(ABORT, 'fixture'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        s.write(sample("q_000000000003"))
    assert s.read("q_000000000001") is not None
    assert s.read("q_000000000002") is not None
    assert s.read("q_000000000003") is None


def test_payload_size_limit_before_file_creation(tmp_path):
    from app.audit.store import UsageAuditStore

    record = sample()
    attempt = record.usage["attempts"][0]
    attempt["prompt_tokens"] = attempt["completion_tokens"] = 10**1000
    record.usage["attempts"] = [dict(attempt) for _ in range(1000)]
    path = tmp_path / "audit.db"
    with pytest.raises(ValueError, match="size limit"):
        UsageAuditStore(str(path), 10).write(record)
    assert not path.exists()


async def test_http_timeout_is_still_504_and_audit_is_cancelled(
    connected_query, audit_path, monkeypatch
):
    monkeypatch.setattr(settings, "query_timeout_seconds", 0.02)
    r = native_router(max_retries=0)
    attempts = []

    async def wait(*args, **kwargs):
        attempts.append(True)
        await asyncio.Event().wait()

    r._call_provider = wait
    monkeypatch.setattr(query, "get_default_router", lambda: r)
    response = connected_query.post(
        "/api/v1/query",
        json={
            "nl_text": "private timeout",
            "database": "contract",
            "num_candidates": 1,
        },
    )
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "QUERY_TIMEOUT"
    with sqlite3.connect(audit_path) as conn:
        record = json.loads(conn.execute("SELECT payload FROM llm_usage_audit").fetchone()[0])
    assert record["outcome"] == "cancelled"
    # The deadline may expire during schema setup, before a provider is entered.
    assert record["usage"]["attempt_count"] == len(attempts)
    assert record["llm_calls"] == len(attempts)
    assert "private timeout" not in str(record)


async def test_snapshot_failure_does_not_replace_success(audit_path, monkeypatch):
    async def done(*args, **kwargs):
        def broken():
            raise ValueError("private snapshot failure")

        kwargs["audit"].collector.summary = broken
        return {"status": "completed"}

    monkeypatch.setattr(query_service, "_run_query", done)
    result = await query_service.run_query(query.QueryRequest(nl_text="test"))
    assert result["status"] == "completed"
    assert result["llm_audit"]["status"] == "failed"
    assert "private" not in str(result)
    assert not audit_path.exists()
