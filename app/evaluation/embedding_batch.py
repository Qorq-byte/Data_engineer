"""Data-only preflight for one bounded local embedding run; never imports an SDK."""

from __future__ import annotations

import hashlib
import platform
import re
from importlib.metadata import version
from pathlib import Path
from typing import Any

from app.evaluation import acceptance_data as data
from app.evaluation.artifacts import read_json
from app.evaluation.predictions import fingerprint

ROOT = Path(__file__).resolve().parents[2]
PROFILE = "local-embeddinggemma-acceptance-v1"
ACCEPTANCE_SHA = "ed4be1acd589350adf428491ae4db542ee686fd3bd212a3f2f3517dca6b45755"
BASE_URL = "http://127.0.0.1:11434"
MODEL = "embeddinggemma:latest"
DIMENSION = 768
_SOURCE_NAMES = (
    "app/evaluation/embedding_batch.py", "app/evaluation/embedding_runtime.py",
    "app/evaluation/embedding_rankings.py", "app/evaluation/embedding_probe.py",
    "scripts/run_embedding_acceptance.py",
    "tests/fixtures/embedding_acceptance/schema_prompt.yml",
    "app/knowledge/retrieval/embedding.py", "app/knowledge/retrieval/schema_rag.py",
    "app/knowledge/retrieval/lancedb_store.py", "app/knowledge/retrieval/bm25_index.py",
    "app/knowledge/retrieval/rrf_fusion.py", "app/knowledge/schema_retriever.py",
    "app/nodes/schema_linking.py", "app/nodes/generate_sql.py", "app/nodes/base.py",
    "app/nodes/registry.py", "app/harness/runner.py", "app/harness/plan_loader.py",
    "app/evaluation/acceptance_data.py", "app/evaluation/acceptance_retrieval.py",
)


def source_paths() -> list[Path]:
    return [ROOT / name for name in _SOURCE_NAMES] + data.source_paths()


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_inputs() -> tuple[dict[str, Any], list[dict[str, str]]]:
    if fingerprint(data.verify_manifest()) != ACCEPTANCE_SHA:
        raise ValueError("The approved acceptance profile has changed")
    suite = data.validate_retrieval(read_json(data.DIRECTORY / "retrieval.json"))
    inputs = [{"id": f"document/{doc['id']}", "text": doc["text"]}
              for doc in suite["documents"]] + [
        {"id": f"query/{query['id']}", "text": query["query"]} for query in suite["queries"]
    ]
    if len(inputs) != 53 or len({row["text"] for row in inputs}) != 53:
        raise ValueError("Unexpected embedding input count")
    return suite, inputs


def read_model_identity(path: Path) -> dict[str, Any]:
    value = read_json(path, max_bytes=1024 * 1024)
    if not isinstance(value, dict) or not isinstance(value.get("layers"), list):
        raise ValueError("Invalid cached Ollama model manifest")
    layers = [layer for layer in value["layers"] if isinstance(layer, dict)
              and layer.get("mediaType") == "application/vnd.ollama.image.model"]
    if len(layers) != 1:
        raise ValueError("Expected one local model layer")
    raw = path.read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError("Model manifest exceeds limit")
    return _model({"provider": "ollama", "name": MODEL, "dimension": DIMENSION,
                   "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                   "layer_digest": layers[0].get("digest"), "layer_bytes": layers[0].get("size")})


def _model(value: object) -> dict[str, Any]:
    model = data.fields(value, "provider name dimension digest layer_digest layer_bytes")
    if (model["provider"] != "ollama" or model["name"] != MODEL
            or type(model["dimension"]) is not int or model["dimension"] != DIMENSION
            or type(model["layer_bytes"]) is not int or not 1 <= model["layer_bytes"] <= 2**30):
        raise ValueError("Unsupported bounded local embedding model")
    for key in ("digest", "layer_digest"):
        if not isinstance(model[key], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", model[key]):
            raise ValueError("Invalid local model identity")
    return model


def prepare(run_name: str, model: object) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", run_name):
        raise ValueError("Invalid embedding run name")
    model = _model(model)
    _, inputs = load_inputs()
    inventory: list[dict[str, Any]] = [{"id": row["id"], "text_sha256": text_sha(row["text"]),
                  "utf8_bytes": len(row["text"].encode("utf-8"))} for row in inputs]
    total = sum(row["utf8_bytes"] for row in inventory)
    if total != 3803 or max(row["utf8_bytes"] for row in inventory) != 165:
        raise ValueError("Frozen embedding input size changed")
    return {
        "profile": PROFILE, "run_name": run_name, "model": model, "base_url": BASE_URL,
        "acceptance_manifest_sha256": ACCEPTANCE_SHA, "inputs": inventory,
        "runtime": {"python": platform.python_version(), **{name: version(name) for name in
                    ("lancedb", "pyarrow", "httpx", "jieba", "sqlglot")}},
        "source_sha256": {str(path.relative_to(ROOT)).replace("\\", "/"):
                          hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                          for path in source_paths()},
        "limits": {"embedding_posts": 53, "metadata_gets": 2, "http_requests": 55,
                   "concurrency": 1, "retries": 0, "request_seconds": 30,
                   "metadata_seconds": 10, "run_seconds": 600,
                   "input_utf8_bytes": total, "max_text_bytes": 165,
                   "max_response_bytes": 262144, "max_artifact_bytes": 64 * 1024 * 1024,
                   "float32_vector_payload_bytes": 53 * DIMENSION * 4},
        "retrieval": {"k": 3, "rrf_k": 60, "alpha": 0.7, "warm_repetitions": 3,
                      "corpus": "one shared 23-document corpus",
                      "domain_filter": "frozen document IDs and sparse metadata; "
                                       "not native domain API"},
        "requests": ([{"kind": "model_before", "method": "GET", "path": "/api/tags"}]
                     + [{"kind": "embedding", "method": "POST", "path": "/api/embeddings",
                         "input_id": row["id"]} for row in inventory]
                     + [{"kind": "model_after", "method": "GET", "path": "/api/tags"}]),
        "resource_scope": "Supervised worker600s plus cleanup5s; requests/inputs bounded. "
                          "Artifact64MiB checked after phases, not a filesystem quota. "
                          "Existing Ollama CPU/GPU/RSS not hard-limited; startup not reset.",
        "actual_cost_usd": None,
    }


def manifest_digest(value: object) -> str:
    return fingerprint(value)


def validate(value: object, confirmation: str) -> dict[str, Any]:
    if not isinstance(value, dict) or manifest_digest(value) != confirmation:
        raise ValueError("Explicit matching manifest SHA256 is required")
    name = value.get("run_name")
    if not isinstance(name, str):
        raise ValueError("Invalid embedding run name")
    expected = prepare(name, value.get("model"))
    if manifest_digest(expected) != manifest_digest(value):
        raise ValueError("Embedding manifest, source or frozen inputs changed")
    return expected
