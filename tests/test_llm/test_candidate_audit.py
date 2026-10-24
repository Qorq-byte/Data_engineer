"""Candidate digests bind supplied SQL to an opt-in snapshot, without storing SQL."""

import asyncio
import copy
import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app.api import query
from app.audit.store import UsageAuditRecord, UsageAuditStore
from app.config.settings import settings
from app.evaluation.predictions import (
    fingerprint,
    validate_exported_predictions,
)
from app.workflow import query_service
from tests.test_api import test_query_contract as contracts
from tests.test_llm.test_sdk_boundary import native_router
from tests.test_rag.test_prediction_export import binding, build, dataset, response

connected_query = contracts.connected_query
ROOT = Path(__file__).resolve().parents[2]


def captured(source=None, *, version=2):
    source = response() if source is None else source
    record = {
        "schema_version": version,
        "query_id": source["query_id"],
        "outcome": source["status"],
        "llm_calls": source["llm_calls"],
        "usage": source["llm_usage"],
    }
    if version == 2:
        from app.audit.candidates import candidate_digests

        record.update(
            candidate_digest_version="candidate-binding-v1",
            candidate_digests=candidate_digests(source["query_id"], source["candidates"]),
        )
    return record


def record(source=None, *, bound=True):
    source = response() if source is None else source
    kwargs = {}
    if bound:
        from app.audit.candidates import candidate_digests

        kwargs["candidate_digests"] = candidate_digests(source["query_id"], source["candidates"])
    return UsageAuditRecord(
        query_id=source["query_id"],
        session_id="private session",
        outcome=source["status"],
        llm_calls=source["llm_calls"],
        usage=source["llm_usage"],
        **kwargs,
    )


def test_digest_is_exact_utf8_and_does_not_store_private_fields(tmp_path):
    from app.audit.candidates import candidate_digest

    source = response()
    candidate = source["candidates"][1]
    candidate.update(id="private-candidate", db_id="private-target", sql_text="SELECT '秘密值'")
    before = copy.deepcopy(source)
    digest = candidate_digest(source["query_id"], candidate)
    assert len(digest) == 64 and int(digest, 16) >= 0
    path = tmp_path / "audit.sqlite3"
    store = UsageAuditStore(str(path))
    store.write(record(source))
    saved = store.read(source["query_id"])
    assert saved["schema_version"] == 2
    assert digest in saved["candidate_digests"]
    assert source == before
    for private in [candidate["sql_text"], candidate["db_id"], candidate["id"], "private prompt"]:
        assert private not in str(saved)
        assert private.encode("utf-8") not in path.read_bytes()


@pytest.mark.parametrize(
    "field", ["query", "id", "db_id", "dialect", "sql_text", "generation_source", "finish_reason"]
)
def test_each_bound_field_changes_digest(field):
    from app.audit.candidates import candidate_digest

    source = response()
    candidate = source["candidates"][1]
    first = candidate_digest(source["query_id"], candidate)
    second_query = source["query_id"]
    if field == "query":
        second_query = "q_000000000002"
    else:
        candidate[field] = {
            "id": "other",
            "db_id": "other",
            "dialect": "duckdb",
            "sql_text": "SELECT 1 ",
            "generation_source": "model_stream",
            "finish_reason": "stop",
        }[field]
    assert candidate_digest(second_query, candidate) != first


@pytest.mark.parametrize("mode", ["duplicate", "too_many", "byte_limit", "total_bytes", "unsafe"])
def test_capture_rejects_duplicates_bounds_and_unsafe_fields(mode):
    from app.audit.candidates import candidate_digests

    source = response()
    candidates = source["candidates"]
    if mode == "duplicate":
        candidates.append(dict(candidates[0]))
    elif mode == "too_many":
        candidates = [dict(candidates[0], id=str(i)) for i in range(1001)]
    elif mode == "byte_limit":
        candidates[0]["sql_text"] = "é" * (524288 + 1)
    elif mode == "total_bytes":
        candidates = [dict(candidates[0], id=str(i), sql_text="x" * 1048576) for i in range(17)]
    else:
        candidates[0]["tool_calls"] = "private tool arguments"
    with pytest.raises(ValueError) as failure:
        candidate_digests(source["query_id"], candidates)
    assert "private" not in str(failure.value)


def test_v1_v2_same_database_retention_and_conflicts(tmp_path):
    path = tmp_path / "audit.sqlite3"
    store = UsageAuditStore(str(path), max_records=2)
    old = record(bound=False)
    store.write(old)
    second = response()
    second["query_id"] = "q_000000000002"
    new = record(second)
    store.write(new)
    store.write(new)
    assert store.read(old.query_id)["schema_version"] == 1
    assert store.read(new.query_id)["schema_version"] == 2
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    with pytest.raises(ValueError, match="Conflicting"):
        store.write(replace(new, candidate_digests=["f" * 64]))
    third = replace(new, query_id="q_000000000003")
    store.write(third)
    assert store.read(old.query_id) is None and store.read(new.query_id)


def test_bound_export_v2_roundtrip_and_legacy_stays_v1():
    source = response()
    source["candidates"][1].update(generation_source="model_stream", finish_reason="stop")
    audit = captured(source)
    rows = build(responses=[source], audit_lookup=lambda _: audit, require_candidate_audit=True)
    row = rows[0]
    assert row["export_version"] == "request-prediction-v2"
    assert row["provenance"]["audit_match"] == "candidate_match"
    assert row["provenance"]["sql_binding"] == "candidate_digest_matched"
    assert row["provenance"]["candidate_sha256"] in audit["candidate_digests"]
    assert row["finish_reason"] == "stop"
    validate_exported_predictions(dataset()["sql_cases"], rows, dataset=dataset())
    assert build()[0]["export_version"] == "request-prediction-v1"
    legacy_source = response()
    old = build(
        responses=[legacy_source], audit_lookup=lambda _: captured(legacy_source, version=1)
    )[0]
    assert old["provenance"]["audit_match"] == "metadata_match"


@pytest.mark.parametrize("field", ["sql_text", "db_id", "generation_source", "finish_reason"])
def test_v2_never_downgrades_when_selected_candidate_is_changed(field):
    source = response()
    audit = captured(source)
    candidate = source["candidates"][1]
    selected = binding()
    candidate[field] = {
        "sql_text": "SELECT 999",
        "db_id": "other",
        "generation_source": "model_response",
        "finish_reason": "stop",
    }[field]
    if field == "db_id":
        selected["db_id"] = "other"
    with pytest.raises(ValueError, match="candidate|Candidate"):
        build(responses=[source], bindings=[selected], audit_lookup=lambda _: audit)


@pytest.mark.parametrize(
    "mode", ["version", "digest_version", "missing", "bad_hash", "duplicate", "empty"]
)
def test_bad_audit_candidate_metadata_is_rejected(mode):
    source = response()
    audit = captured(source)
    if mode == "version":
        audit["schema_version"] = 3
    elif mode == "digest_version":
        audit["candidate_digest_version"] = "future"
    elif mode == "missing":
        del audit["candidate_digests"]
    elif mode == "bad_hash":
        audit["candidate_digests"] = ["invalid"]
    elif mode == "empty":
        audit["candidate_digests"] = []
    else:
        audit["candidate_digests"] *= 2
    with pytest.raises(ValueError):
        build(responses=[source], audit_lookup=lambda _: audit)


@pytest.mark.parametrize("lookup", [None, "v1"])
def test_strict_binding_requires_audit_and_v2_for_completed_predictions(lookup):
    with pytest.raises(ValueError):
        build(
            audit_lookup=(lambda _: captured(version=1)) if lookup else None,
            require_candidate_audit=True,
        )


def test_failed_prediction_is_still_a_failure_with_strict_audit():
    source = response()
    source.update(status="failed", candidates=[])
    selected = binding()
    del selected["candidate_id"]
    rows = build(
        responses=[source],
        bindings=[selected],
        audit_lookup=lambda _: captured(source),
        require_candidate_audit=True,
    )
    assert rows[0]["status"] == "failed" and rows[0]["sql"] is None
    assert rows[0]["provenance"]["audit_match"] == "metadata_match"


@pytest.mark.parametrize(
    "field", ["sql", "candidate_sha256", "candidate_finish_reason", "sql_binding"]
)
def test_consumer_checks_candidate_binding_even_after_row_hash_is_recomputed(field):
    source = response()
    rows = build(responses=[source], audit_lookup=lambda _: captured(source))
    row = rows[0]
    if field == "sql":
        row["sql"] = "SELECT 999"
    else:
        row["provenance"][field] = {
            "candidate_sha256": "f" * 64,
            "candidate_finish_reason": "stop",
            "sql_binding": "caller_supplied_not_verified_by_audit",
        }[field]
    row["export_sha256"] = fingerprint({k: v for k, v in row.items() if k != "export_sha256"})
    with pytest.raises(ValueError):
        validate_exported_predictions(dataset()["sql_cases"], rows)


@pytest.mark.parametrize("protocol", ["json", "sse", "mcp"])
async def test_protocols_capture_final_candidates_without_raw_sql(
    connected_query, monkeypatch, tmp_path, protocol
):
    from app.audit.candidates import candidate_digests

    path = tmp_path / "audit.sqlite3"
    monkeypatch.setattr(settings, "llm_audit_path", str(path))
    monkeypatch.setattr(settings, "llm_audit_candidate_digests", True)
    if protocol == "mcp":
        result = await contracts.mcp_query(nl_text="find all orders", db_id="contract")
    else:
        http = connected_query.post(
            "/api/v1/query" + ("/stream" if protocol == "sse" else ""),
            json={
                "nl_text": "find all orders",
                "database": "contract",
                "execute": True,
                "num_candidates": 1,
            },
        )
        result = (
            next(v for e, v in contracts.stream_events(http) if e == "done")
            if protocol == "sse"
            else http.json()
        )
    assert result["status"] == "completed"
    saved = UsageAuditStore(str(path)).read(result["query_id"])
    assert saved["candidate_digests"] == candidate_digests(result["query_id"], result["candidates"])
    assert result["llm_audit"]["status"] == "persisted"
    assert b"SELECT id FROM orders" not in path.read_bytes()
    admin = connected_query.get(f"/api/v1/usage-audits/{result['query_id']}")
    assert admin.status_code == 200 and admin.json()["schema_version"] == 2


async def test_enabled_capture_with_disabled_store_does_not_create_file(
    connected_query, monkeypatch
):
    monkeypatch.setattr(settings, "llm_audit_path", "")
    monkeypatch.setattr(settings, "llm_audit_candidate_digests", True)
    result = connected_query.post(
        "/api/v1/query", json={"nl_text": "orders", "database": "contract"}
    ).json()
    assert result["llm_audit"]["status"] == "disabled"


async def test_digest_failure_cannot_overwrite_query_result(connected_query, monkeypatch, tmp_path):
    import app.workflow.query_audit as audit_module

    monkeypatch.setattr(settings, "llm_audit_path", str(tmp_path / "audit.sqlite3"))
    monkeypatch.setattr(settings, "llm_audit_candidate_digests", True)

    def fail(*args):
        raise ValueError("private SQL failure")

    monkeypatch.setattr(audit_module, "candidate_digests", fail)
    result = connected_query.post(
        "/api/v1/query",
        json={"nl_text": "find all orders", "database": "contract", "execute": True},
    ).json()
    assert result["status"] == "completed" and result["llm_audit"]["status"] == "failed"
    assert "private SQL failure" not in str(result)


async def test_real_sdk_query_to_bound_cli_and_sqlite_evaluation(
    connected_query, monkeypatch, tmp_path
):
    import litellm

    from scripts.evaluate_quality import evaluate

    path = tmp_path / "audit.sqlite3"
    monkeypatch.setattr(settings, "llm_audit_path", str(path))
    monkeypatch.setattr(settings, "llm_audit_candidate_digests", True)

    async def transport(**kwargs):
        return litellm.ModelResponse(
            model="fixture",
            usage={"prompt_tokens": 3, "completion_tokens": 1},
            choices=[
                {"finish_reason": "stop", "message": {"role": "assistant", "content": "SELECT 1"}}
            ],
        )

    monkeypatch.setattr(litellm, "acompletion", transport)
    monkeypatch.setattr(query, "get_default_router", lambda: native_router(max_retries=0))
    source = connected_query.post(
        "/api/v1/query",
        json={
            "nl_text": "find all orders",
            "database": "contract",
            "num_candidates": 1,
        },
    ).json()
    selected = {
        "id": "one",
        "query_id": source["query_id"],
        "candidate_id": source["candidates"][0]["id"],
        "db_id": "contract",
    }
    data = json.loads((ROOT / "tests/fixtures/retrieval_eval.json").read_text(encoding="utf-8"))
    data["sql_cases"] = dataset()["sql_cases"]
    inputs = {"dataset": data, "responses": [source], "bindings": [selected]}
    argv = [sys.executable, "-m", "scripts.export_predictions"]
    for name, value in inputs.items():
        file = tmp_path / f"{name}.json"
        file.write_text(json.dumps(value), encoding="utf-8")
        argv.extend([f"--{name}", str(file)])
    output = tmp_path / "predictions.json"
    argv.extend(["--output", str(output), "--audit-db", str(path), "--require-candidate-audit"])
    run = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    predictions = json.loads(output.read_text())
    assert predictions[0]["provenance"]["audit_match"] == "candidate_match"
    metrics = evaluate(data, 3, predictions)["sql_model_metrics"]
    assert metrics["case_count"] == 2 and metrics["result_correct_count"] == 1
    # A mismatch must leave an existing export intact.
    original = output.read_bytes()
    inputs["responses"][0]["candidates"][0]["sql_text"] = "SELECT 999"
    (tmp_path / "responses.json").write_text(json.dumps(inputs["responses"]), encoding="utf-8")
    failed = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert failed.returncode == 2 and output.read_bytes() == original
    assert "999" not in failed.stderr and "SELECT" not in failed.stderr

    without_audit = list(argv)
    at = without_audit.index("--audit-db")
    del without_audit[at : at + 2]
    failed = subprocess.run(without_audit, cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert failed.returncode == 2 and output.read_bytes() == original


@pytest.mark.parametrize("source", ["unknown", "model_response"])
def test_missing_default_evidence_is_canonical_for_bound_export(source):
    item = response()
    candidate = item["candidates"][1]
    candidate["generation_source"] = source
    audit = captured(item)
    candidate.pop("finish_reason", None)
    rows = build(responses=[item], audit_lookup=lambda _: audit)
    assert rows[0]["finish_reason"] is None
    assert rows[0]["provenance"]["audit_match"] == "candidate_match"
    validate_exported_predictions(dataset()["sql_cases"], rows)


async def test_v2_cancellation_waits_for_cleanup_and_records_no_final_candidates(
    monkeypatch, tmp_path
):
    path = tmp_path / "cancel.sqlite3"
    monkeypatch.setattr(settings, "llm_audit_path", str(path))
    monkeypatch.setattr(settings, "llm_audit_candidate_digests", True)
    entered = asyncio.Event()
    closed = []

    async def run(*args, **kwargs):
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    monkeypatch.setattr(query_service, "_run_query", run)
    task = asyncio.create_task(query_service.run_query(query.QueryRequest(nl_text="private input")))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]
    with sqlite3.connect(path) as conn:
        payload = json.loads(conn.execute("SELECT payload FROM llm_usage_audit").fetchone()[0])
    assert payload["schema_version"] == 2 and payload["outcome"] == "cancelled"
    assert payload["candidate_digests"] == [] and "private input" not in str(payload)
    assert UsageAuditStore(str(path)).read(payload["query_id"]) == payload


async def test_v2_business_failure_remains_unbound_failed_prediction(
    connected_query,
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "failed.sqlite3"
    monkeypatch.setattr(settings, "llm_audit_path", str(path))
    monkeypatch.setattr(settings, "llm_audit_candidate_digests", True)
    router = native_router(max_retries=0)

    async def transport(*args, **kwargs):
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "SELECT missing_column FROM orders",
                    },
                }
            ]
        }

    router._call_provider = transport
    monkeypatch.setattr(query, "get_default_router", lambda: router)
    item = connected_query.post(
        "/api/v1/query",
        json={
            "nl_text": "orders",
            "database": "contract",
            "execute": True,
            "num_candidates": 1,
        },
    ).json()
    assert item["status"] == "failed" and item["candidates"]
    saved = UsageAuditStore(str(path)).read(item["query_id"])
    assert saved["schema_version"] == 2 and len(saved["candidate_digests"]) == 1
    selection = {
        "id": "one",
        "query_id": item["query_id"],
        "candidate_id": item["candidates"][0]["id"],
        "db_id": "contract",
    }
    row = build(
        responses=[item],
        bindings=[selection],
        audit_lookup=lambda _: saved,
        require_candidate_audit=True,
    )[0]
    assert row["status"] == "failed" and row["sql"] is None
    assert row["export_version"] == "request-prediction-v1"
    assert row["provenance"]["audit_match"] == "metadata_match"


def test_corrupt_stored_v2_is_rejected_read_only(tmp_path):
    path = tmp_path / "audit.sqlite3"
    store = UsageAuditStore(str(path))
    snapshot = record()
    store.write(snapshot)
    with sqlite3.connect(path) as conn:
        saved = store.read(snapshot.query_id)
        saved["candidate_digest_version"] = "future"
        conn.execute("UPDATE llm_usage_audit SET payload=?", (json.dumps(saved),))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        store.read(snapshot.query_id)
    assert path.read_bytes() == before
