"""Durable budgets, fail-closed manifests and worker cleanup without model requests."""

import asyncio
import copy
import subprocess

import httpx
import pytest

from app.evaluation import embedding_runtime as runtime
from app.evaluation.artifacts import read_json
from app.evaluation.embedding_batch import (
    DIMENSION,
    manifest_digest,
    prepare,
    validate,
)


@pytest.fixture
def manifest():
    return prepare("runtime-control", {"provider": "ollama", "name": "embeddinggemma:latest",
        "dimension": DIMENSION, "digest": "sha256:" + "a" * 64,
        "layer_digest": "sha256:" + "b" * 64, "layer_bytes": 621867104})


@pytest.mark.parametrize("key,value", [("concurrency", True), ("embedding_posts", 54),
                                      ("run_seconds", 601), ("retries", 1)])
def test_manifest_resigning_cannot_expand_fixed_profile(manifest, key, value):
    manifest["limits"][key] = value
    with pytest.raises(ValueError, match="changed"):
        validate(manifest, manifest_digest(manifest))


def test_manifest_requires_exact_confirmation_and_source_identity(manifest):
    with pytest.raises(ValueError, match="matching"):
        validate(manifest, "0" * 64)
    manifest["source_sha256"]["app/evaluation/embedding_runtime.py"] = "0" * 64
    with pytest.raises(ValueError, match="changed"):
        validate(manifest, manifest_digest(manifest))


@pytest.mark.parametrize("name", ["../escape", "UPPER", "a/b", "", "x" * 49])
def test_run_name_cannot_escape_root(manifest, name):
    with pytest.raises(ValueError):
        prepare(name, manifest["model"])


@pytest.mark.parametrize("vector", [[1.] * 3, [0.] * DIMENSION,
                                     [float("nan")] * DIMENSION, [True] * DIMENSION])
def test_vector_rejects_invalid_payload(vector):
    with pytest.raises(runtime.MeasurementError):
        runtime.validate_vector(vector)


def test_vector_artifact_identity_and_input_order_are_enforced(manifest):
    records = [{"id": row["id"], "text_sha256": row["text_sha256"],
                "vector": [1.] + [0.] * (DIMENSION - 1)} for row in manifest["inputs"]]
    artifact = runtime.vector_artifact(manifest, records)
    assert len(runtime.validate_vectors(artifact, manifest)) == 53
    for changed in ("model", "order", "payload"):
        mutant = copy.deepcopy(artifact)
        if changed == "model":
            mutant["model"]["digest"] = "sha256:" + "c" * 64
        elif changed == "order":
            mutant["records"].reverse()
        else:
            mutant["records"][0]["vector"][0] = 0.
        from app.evaluation.predictions import fingerprint
        mutant["artifact_sha256"] = fingerprint(
            {k: v for k, v in mutant.items() if k != "artifact_sha256"})
        with pytest.raises(runtime.MeasurementError):
            runtime.validate_vectors(mutant, manifest)


def test_ledger_preserves_unknown_transport_and_rejects_overbudget(manifest, tmp_path):
    ledger = runtime.RequestLedger(tmp_path, manifest)
    with pytest.raises(runtime.MeasurementError, match="ORDER"):
        ledger.reserve("embedding", "unexpected")
    for row in manifest["requests"]:
        ledger.reserve(row["kind"], row.get("input_id"))
    with pytest.raises(runtime.MeasurementError, match="BUDGET"):
        ledger.reserve("model_after")
    assert ledger.summary()["unknown_transport_outcomes"] == 55
    assert len(read_json(tmp_path / "requests.json")) == 55


async def test_reservation_failure_prevents_wire_request(manifest, tmp_path, monkeypatch):
    calls = []
    ledger = runtime.RequestLedger(tmp_path, manifest)
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(runtime, "write_atomic", fail)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: calls.append(request))) as client:
        with pytest.raises(OSError):
            await runtime.BoundedOllamaClient(client, ledger).verify_model("model_before")
    assert calls == []


@pytest.mark.parametrize("defect", ["digest", "oversize", "http", "cancel"])
async def test_bad_metadata_retains_reservation_without_retry(manifest, tmp_path, defect):
    calls = []
    async def respond(request):
        calls.append(request)
        if defect == "cancel":
            raise asyncio.CancelledError
        if defect == "oversize":
            return httpx.Response(200, content=b"x" * 262145)
        if defect == "http":
            return httpx.Response(500)
        return httpx.Response(200, json={"models": [{"name": "embeddinggemma:latest",
                                                     "digest": "wrong"}]})
    ledger = runtime.RequestLedger(tmp_path, manifest)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        bounded = runtime.BoundedOllamaClient(client, ledger)
        with pytest.raises((runtime.MeasurementError, httpx.HTTPStatusError,
                            asyncio.CancelledError)):
            await bounded.verify_model("model_before")
    assert len(calls) == 1
    assert ledger.summary()["requests_validated"] == 0


async def test_unapproved_input_never_reserves_or_sends(manifest, tmp_path):
    ledger = runtime.RequestLedger(tmp_path, manifest)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: pytest.fail("wire access"))) as client:
        with pytest.raises(runtime.MeasurementError, match="UNAPPROVED"):
            await runtime.BoundedOllamaClient(client, ledger).post(
                "http://127.0.0.1:11434/api/embeddings",
                json={"model": "embeddinggemma:latest", "prompt": "not frozen"})
    assert ledger.records == []


@pytest.mark.parametrize("failure", ["start", "timeout", "invalid_report"])
def test_supervisor_failure_cleanup_and_exclusive_run(manifest, tmp_path, monkeypatch, failure):
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(runtime, "RUN_ROOT", tmp_path / "runs")
    killed = []
    class Child:
        def wait(self, timeout):
            if failure == "timeout" and not killed:
                raise subprocess.TimeoutExpired("synthetic", timeout)
            return 0
        def kill(self):
            killed.append(True)
    def spawn(*args, **kwargs):
        assert "OPENAI_API_KEY" not in kwargs["env"]
        if failure == "start":
            raise OSError("synthetic spawn failure")
        return Child()
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    result = runtime.supervise(manifest, manifest_digest(manifest))
    assert result["status"] == "failed" and result["index_ready"] is False
    assert bool(killed) == (failure == "timeout")
    state = read_json(tmp_path / "runs/runtime-control/run-state.json")
    assert state["state"] == "failed"
    with pytest.raises(FileExistsError):
        runtime.supervise(manifest, manifest_digest(manifest))


def test_prepare_imports_no_provider_or_http_client_in_clean_process(tmp_path):
    code = """
import sys
from app.evaluation.embedding_batch import prepare
prepare('import-control', {'provider':'ollama','name':'embeddinggemma:latest',
 'dimension':768,'digest':'sha256:'+'a'*64,'layer_digest':'sha256:'+'b'*64,'layer_bytes':1})
assert not any(k in sys.modules for k in ('httpx','openai','lancedb',
 'app.knowledge.retrieval.embedding','app.evaluation.embedding_runtime'))
"""
    import sys
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")


@pytest.mark.parametrize("exception,code", [
    (httpx.ConnectError("private detail"), "HTTP_CONNECT_FAILED"),
    (httpx.ReadTimeout("private detail"), "HTTP_TIMEOUT"),
    (PermissionError("private path"), "ARTIFACT_IO_FAILED"),
    (asyncio.CancelledError(), "CANCELLED"),
    (TimeoutError(), "DEADLINE_EXCEEDED"),
    (ValueError("private response"), "INVALID_RESPONSE_OR_ARTIFACT"),
])
def test_failure_classification_never_embeds_private_details(exception, code):
    assert runtime.failure_code(exception) == code


@pytest.mark.parametrize("persistent", [True, False])
def test_windows_publication_retry_is_bounded_and_local(tmp_path, monkeypatch, persistent):
    calls = []
    pauses = []
    original = runtime._atomic_write
    def replace(output, rows):
        calls.append(True)
        if persistent or len(calls) < 3:
            exc = PermissionError("scanner lock")
            exc.winerror = 32
            raise exc
        original(output, rows)
    monkeypatch.setattr(runtime, "_atomic_write", replace)
    monkeypatch.setattr(runtime.time, "sleep", pauses.append)
    target = tmp_path / "ledger.json"
    if persistent:
        with pytest.raises(PermissionError):
            runtime.write_atomic(target, [])
        assert len(calls) == 5 and not target.exists()
    else:
        runtime.write_atomic(target, [])
        assert len(calls) == 3 and read_json(target) == []
    assert sum(pauses) <= .2


@pytest.mark.parametrize("after_digest_valid", [True, False])
async def test_complete_collection_checks_final_model_before_publishing_vectors(
        manifest, tmp_path, monkeypatch, after_digest_valid):
    from app.evaluation import embedding_rankings
    from app.evaluation.artifacts import write_atomic

    directory = tmp_path / "run"
    directory.mkdir()
    owner = "a" * 32
    confirmation = manifest_digest(manifest)
    write_atomic(directory / "run-state.json", {"owner": owner,
                 "manifest_sha256": confirmation, "state": "starting"})
    monkeypatch.setattr(runtime, "run_directory", lambda name: directory)
    requests = []
    raw_client = httpx.AsyncClient
    def response(request):
        requests.append(request)
        if request.url.path == "/api/tags":
            digest = manifest["model"]["digest"]
            if len(requests) == 55 and not after_digest_valid:
                digest = "sha256:" + "b" * 64
            return httpx.Response(200, json={"models": [{"name": "embeddinggemma:latest",
                                                         "digest": digest}]})
        return httpx.Response(200, json={"embedding": [1.] + [0.] * (DIMENSION - 1)})
    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return raw_client(transport=httpx.MockTransport(response))
    async def measure(*args):
        assert (directory / "vectors.json").exists()
        return {"index_ready": True, "technical_errors": []}
    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(embedding_rankings, "measure_rankings", measure)
    if after_digest_valid:
        report = await runtime.worker(manifest, confirmation, owner)
        assert report["status"] == "completed"
        assert report["requests"]["requests_validated"] == 55
        assert report["actual_cost_usd"] is None
    else:
        with pytest.raises(runtime.MeasurementError, match="MODEL_IDENTITY"):
            await runtime.worker(manifest, confirmation, owner)
        assert not (directory / "vectors.json").exists()
        assert read_json(directory / "report.json")["status"] == "failed"
    assert len(requests) == 55
    with pytest.raises(runtime.MeasurementError, match="OWNERSHIP"):
        await runtime.worker(manifest, confirmation, owner)
    assert len(requests) == 55
