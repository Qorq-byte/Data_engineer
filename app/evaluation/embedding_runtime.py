"""One-shot local embedding collection with a supervised worker and durable reservations."""

from __future__ import annotations

import asyncio
import math
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.evaluation.artifacts import read_json
from app.evaluation.artifacts import write_atomic as _atomic_write
from app.evaluation.embedding_batch import (
    BASE_URL,
    DIMENSION,
    MODEL,
    ROOT,
    load_inputs,
    manifest_digest,
    text_sha,
    validate,
)
from app.evaluation.predictions import fingerprint

if TYPE_CHECKING:
    import httpx

RUN_ROOT = ROOT / "data/embedding-acceptance"


def write_atomic(output: Path, rows: object) -> None:
    """Tolerate transient Windows scanner locks; never retry an HTTP operation."""
    for attempt in range(5):
        try:
            _atomic_write(output, rows)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 4:
                raise
            time.sleep(.02 * (attempt + 1))  # At most 200ms, within worker deadline.


class MeasurementError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def run_directory(name: str) -> Path:
    target = (RUN_ROOT / name).resolve()
    if target.parent != RUN_ROOT.resolve():
        raise MeasurementError("INVALID_RUN_DIRECTORY")
    return target


def validate_vector(vector: object) -> list[float]:
    if not isinstance(vector, list) or len(vector) != DIMENSION:
        raise MeasurementError("VECTOR_DIMENSION_MISMATCH")
    try:
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
            raise ValueError
        result = [float(v) for v in vector]
        if not math.isclose(math.hypot(*result), 1.0, abs_tol=1e-5):
            raise ValueError
    except (ValueError, OverflowError) as exc:
        raise MeasurementError("INVALID_VECTOR_VALUES") from exc
    return result


def vector_artifact(manifest: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    value = {"version": "embedding-vectors-v1", "manifest_sha256": manifest_digest(manifest),
             "model": manifest["model"], "inputs_sha256": fingerprint(manifest["inputs"]),
             "records": records}
    return {**value, "artifact_sha256": fingerprint(value)}


def validate_vectors(value: object, manifest: dict[str, Any]) -> dict[str, list[float]]:
    if (not isinstance(value, dict) or set(value) != {
            "version", "manifest_sha256", "model", "inputs_sha256", "records", "artifact_sha256"
    } or value.get("version") != "embedding-vectors-v1"
            or value.get("manifest_sha256") != manifest_digest(manifest)
            or fingerprint(value.get("model")) != fingerprint(manifest["model"])
            or value.get("inputs_sha256") != fingerprint(manifest["inputs"])
            or value.get("artifact_sha256") != fingerprint(
                {k: v for k, v in value.items() if k != "artifact_sha256"})):
        raise MeasurementError("VECTOR_IDENTITY_MISMATCH")
    records = value["records"]
    if not isinstance(records, list) or len(records) != len(manifest["inputs"]):
        raise MeasurementError("VECTOR_INPUT_COUNT_MISMATCH")
    vectors = {}
    for expected, record in zip(manifest["inputs"], records, strict=True):
        if (not isinstance(record, dict) or set(record) != {"id", "text_sha256", "vector"}
                or record["id"] != expected["id"]
                or record["text_sha256"] != expected["text_sha256"]):
            raise MeasurementError("VECTOR_INPUT_IDENTITY_MISMATCH")
        vectors[expected["text_sha256"]] = validate_vector(record["vector"])
    if len(vectors) != 53:
        raise MeasurementError("DUPLICATE_VECTOR_INPUT")
    return vectors


class RequestLedger:
    def __init__(self, directory: Path, manifest: dict[str, Any]):
        self.path = directory / "requests.json"
        self.manifest = manifest
        self.records: list[dict[str, Any]] = []
        write_atomic(self.path, self.records)

    def reserve(self, kind: str, input_id: str | None = None) -> int:
        index = len(self.records)
        if index >= len(self.manifest["requests"]):
            raise MeasurementError("HTTP_BUDGET_EXCEEDED")
        expected = self.manifest["requests"][index]
        if expected["kind"] != kind or expected.get("input_id") != input_id:
            raise MeasurementError("HTTP_REQUEST_ORDER_MISMATCH")
        self.records.append({"sequence": index + 1, **expected, "state": "reserved",
                             "tokens_used": None})
        # No request may reach the wire unless reservation publication succeeded.
        write_atomic(self.path, self.records)
        return index

    def update(self, index: int, **values: Any) -> None:
        self.records[index].update(values)
        write_atomic(self.path, self.records)

    def summary(self) -> dict[str, Any]:
        return {"http_requests_reserved": len(self.records),
                "embedding_posts_reserved": sum(r["kind"] == "embedding" for r in self.records),
                "responses_received": sum(r["state"] != "reserved" for r in self.records),
                "requests_validated": sum(r["state"] == "validated" for r in self.records),
                "unknown_transport_outcomes": sum(r["state"] == "reserved" for r in self.records)}


class BoundedOllamaClient:
    def __init__(self, client: httpx.AsyncClient, ledger: RequestLedger):
        self.client = client
        self.ledger = ledger
        _, inputs = load_inputs()
        self.inventory = {row["text"]: row["id"] for row in inputs}

    async def _request(
        self, kind: str, input_id: str | None = None, payload: dict[str, str] | None = None,
    ) -> httpx.Response:
        import httpx

        index = self.ledger.reserve(kind, input_id)
        expected = self.ledger.manifest["requests"][index]
        seconds = 30 if kind == "embedding" else 10
        started = time.perf_counter()
        async with asyncio.timeout(seconds):
            async with self.client.stream(expected["method"], BASE_URL + expected["path"],
                                          json=payload) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    body.extend(chunk)
                    if len(body) > 262144:
                        raise MeasurementError("HTTP_RESPONSE_LIMIT")
                status = response.status_code
                self.ledger.update(index, state="response_received", http_status=status,
                    response_bytes=len(body), elapsed_ms=(time.perf_counter() - started) * 1000)
                result = httpx.Response(status, content=bytes(body), request=response.request)
                result.raise_for_status()
                return result

    async def post(self, url: str, *, json: dict[str, str]) -> httpx.Response:
        if (url != BASE_URL + "/api/embeddings" or set(json) != {"model", "prompt"}
                or json["model"] != MODEL or json["prompt"] not in self.inventory):
            raise MeasurementError("UNAPPROVED_EMBEDDING_REQUEST")
        return await self._request("embedding", self.inventory[json["prompt"]], json)

    async def verify_model(self, kind: str) -> dict[str, str]:
        response = await self._request(kind)
        value = response.json()
        rows = value.get("models") if isinstance(value, dict) else None
        if not isinstance(rows, list):
            raise MeasurementError("INVALID_MODEL_METADATA")
        matches = [row for row in rows if isinstance(row, dict)
                   and row.get("name", row.get("model")) == MODEL]
        if len(matches) != 1 or not isinstance(matches[0].get("digest"), str):
            raise MeasurementError("MODEL_IDENTITY_MISMATCH")
        digest = "sha256:" + matches[0]["digest"].removeprefix("sha256:")
        if digest != self.ledger.manifest["model"]["digest"]:
            raise MeasurementError("MODEL_IDENTITY_MISMATCH")
        result = {"model": MODEL, "digest": digest}
        self.ledger.update(len(self.ledger.records) - 1, state="validated", model=result)
        return result


def artifact_bytes(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def check_artifact_budget(directory: Path, manifest: dict[str, Any]) -> None:
    # Detection after writes is not a filesystem quota and cannot promise zero overshoot.
    if artifact_bytes(directory) > manifest["limits"]["max_artifact_bytes"]:
        raise MeasurementError("ARTIFACT_BUDGET_EXCEEDED")


def failure_code(exc: BaseException) -> str:
    if isinstance(exc, MeasurementError):
        return exc.code
    if isinstance(exc, TimeoutError):
        return "DEADLINE_EXCEEDED"
    if isinstance(exc, asyncio.CancelledError):
        return "CANCELLED"
    # Classify without retaining response bodies, request texts or credentials.
    import httpx

    if isinstance(exc, httpx.ConnectError):
        return "HTTP_CONNECT_FAILED"
    if isinstance(exc, httpx.TimeoutException):
        return "HTTP_TIMEOUT"
    if isinstance(exc, httpx.HTTPStatusError):
        return "HTTP_STATUS_FAILED"
    if isinstance(exc, httpx.RequestError):
        return "HTTP_TRANSPORT_FAILED"
    if isinstance(exc, OSError):
        return "ARTIFACT_IO_FAILED"
    if isinstance(exc, ValueError):
        return "INVALID_RESPONSE_OR_ARTIFACT"
    return "MEASUREMENT_FAILED"


def _write_failure(directory: Path, manifest: dict[str, Any], code: str) -> dict[str, Any]:
    requests = []
    try:
        value = read_json(directory / "requests.json")
        if isinstance(value, list):
            requests = value
    except Exception:
        pass
    report = {"profile": manifest["profile"], "manifest_sha256": manifest_digest(manifest),
              "status": "failed", "error_code": code, "index_ready": False,
              "embedding_requests_reserved": sum(r.get("kind") == "embedding" for r in requests
                                                 if isinstance(r, dict)),
              "unknown_transport_outcomes": sum(r.get("state") == "reserved" for r in requests
                                                if isinstance(r, dict)),
              "actual_cost_usd": None,
              "missing_quality_reason": "No validated complete result; do not infer quality.",
              "cleanup_scope": "Owned worker/clients only; existing Ollama is not terminated."}
    write_atomic(directory / "report.json", report)
    try:
        state = read_json(directory / "run-state.json")
        if isinstance(state, dict) and state.get("manifest_sha256") == manifest_digest(manifest):
            write_atomic(directory / "run-state.json", {**state, "state": "failed"})
    except Exception:
        pass
    return report


def supervise(value: object, confirmation: str) -> dict[str, Any]:
    manifest = validate(value, confirmation)
    directory = run_directory(manifest["run_name"])
    if not directory.is_relative_to(ROOT.resolve()):
        raise MeasurementError("INVALID_RUN_DIRECTORY")
    directory.mkdir(parents=True, exist_ok=False)  # No resume/retry of unknown transport outcomes.
    owner = uuid.uuid4().hex
    write_atomic(directory / "manifest.json", manifest)
    write_atomic(directory / "run-state.json", {"owner": owner, "manifest_sha256": confirmation,
                                                "state": "starting"})
    workspace = directory / "workspace"
    workspace.mkdir()
    keep = {key: item for key, item in os.environ.items() if key.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
        "USERNAME", "USER", "LOGNAME", "LNAME", "SSL_CERT_FILE", "SSL_CERT_DIR",
    }}
    environment = dict(keep, PYTHONPATH=str(ROOT), PYTHONUTF8="1", LLM_MOCK_MODE="1",
                       RAG_EMBEDDING_PROVIDER="mock", LITELLM_LOCAL_MODEL_COST_MAP="True")
    command = [sys.executable, "-m", "scripts.run_embedding_acceptance", "--worker",
               "--manifest", str(directory / "manifest.json"), "--confirm-manifest-sha256",
               confirmation, "--owner-token", owner]
    with (directory / "worker.log").open("w", encoding="utf-8") as log:
        try:
            child = subprocess.Popen(command, cwd=workspace, env=environment, stdout=log,
                                     stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            return _write_failure(directory, manifest, "WORKER_START_FAILED")
        try:
            code = child.wait(timeout=manifest["limits"]["run_seconds"])
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            child.kill()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return _write_failure(directory, manifest, "WORKER_CLEANUP_FAILED")
            return _write_failure(directory, manifest, "WORKER_DEADLINE_OR_INTERRUPTION")
    if code != 0:
        try:
            failed = read_json(directory / "report.json")
            if (isinstance(failed, dict) and failed.get("status") == "failed"
                    and failed.get("manifest_sha256") == confirmation
                    and failed.get("index_ready") is False):
                return failed
        except Exception:
            pass
        return _write_failure(directory, manifest, "WORKER_FAILED")
    try:
        check_artifact_budget(directory, manifest)
    except MeasurementError:
        return _write_failure(directory, manifest, "ARTIFACT_BUDGET_EXCEEDED")
    try:
        report = read_json(directory / "report.json")
    except Exception:
        return _write_failure(directory, manifest, "INVALID_WORKER_REPORT")
    if (not isinstance(report, dict) or report.get("manifest_sha256") != confirmation
            or report.get("status") != "completed" or not report.get("index_ready")):
        return _write_failure(directory, manifest, "INVALID_WORKER_REPORT")
    return report


async def worker(value: object, confirmation: str, owner: str) -> dict[str, Any]:
    manifest = validate(value, confirmation)
    directory = run_directory(manifest["run_name"])
    state = read_json(directory / "run-state.json")
    if (not isinstance(state, dict) or state != {
            "owner": owner, "manifest_sha256": confirmation, "state": "starting"}
            or not owner or len(owner) != 32):
        raise MeasurementError("INVALID_WORKER_OWNERSHIP")
    write_atomic(directory / "run-state.json", {**state, "state": "running", "pid": os.getpid()})
    ledger = RequestLedger(directory, manifest)
    try:
        import httpx

        from app.evaluation.embedding_rankings import measure_rankings
        from app.knowledge.retrieval.embedding import OllamaEmbeddingProvider

        suite, inputs = load_inputs()
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(30), limits=httpx.Limits(max_connections=1),
                transport=httpx.AsyncHTTPTransport(retries=0)) as raw:
            bounded = BoundedOllamaClient(raw, ledger)
            provider = OllamaEmbeddingProvider(model=MODEL, base_url=BASE_URL,
                                               dimension=DIMENSION, client=bounded)
            before = await bounded.verify_model("model_before")
            records = []
            timings = []
            for row in inputs:
                started = time.perf_counter()
                # Single-text batches prevent gather siblings continuing after a failed request.
                async with asyncio.timeout(30):
                    result = await provider.embed([row["text"]])
                if (result.model != f"ollama:{MODEL}" or result.dimension != DIMENSION
                        or len(result.vectors) != 1):
                    raise MeasurementError("EMBEDDING_RESPONSE_IDENTITY_MISMATCH")
                vector = validate_vector(result.vectors[0])
                records.append({"id": row["id"], "text_sha256": text_sha(row["text"]),
                                "vector": vector})
                elapsed = (time.perf_counter() - started) * 1000
                timings.append({"id": row["id"], "elapsed_ms": elapsed, "tokens_used": None})
                ledger.update(len(ledger.records) - 1, state="validated",
                              vector_sha256=fingerprint(vector), embedding_elapsed_ms=elapsed)
            after = await bounded.verify_model("model_after")
        artifact = vector_artifact(manifest, records)
        validate_vectors(artifact, manifest)
        write_atomic(directory / "vectors.json", artifact)
        check_artifact_budget(directory, manifest)
        measured = await measure_rankings(manifest, artifact, directory, suite)
        if not measured.get("index_ready") or measured.get("technical_errors"):
            raise MeasurementError("INDEX_OR_RETRIEVAL_VALIDATION_FAILED")
        check_artifact_budget(directory, manifest)
        if ledger.summary()["requests_validated"] != 55:
            raise MeasurementError("INCOMPLETE_PHYSICAL_REQUESTS")
        report = {"profile": manifest["profile"], "manifest_sha256": confirmation,
                  "status": "completed", "index_ready": True, "model_before": before,
                  "model_after": after, "requests": ledger.summary(),
                  "vector_artifact_sha256": artifact["artifact_sha256"],
                  "uncached_embedding_timings": timings,
                  "model_startup_state": "Existing service not reset; model may already be loaded",
                  "measured": measured, "real_llm_calls": 0, "actual_cost_usd": None,
                  "resource_scope": manifest["resource_scope"],
                  "artifact_bytes_before_report": artifact_bytes(directory)}
        write_atomic(directory / "report.json", report)
        write_atomic(directory / "run-state.json", {**state, "state": "completed"})
        return report
    except BaseException as exc:
        code = failure_code(exc)
        _write_failure(directory, manifest, code)
        write_atomic(directory / "run-state.json", {**state, "state": "failed"})
        raise
