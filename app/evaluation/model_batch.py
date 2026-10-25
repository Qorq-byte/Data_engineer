"""Public, reproducible authorization manifest. Preparing never imports an LLM SDK."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.evaluation.artifacts import read_json

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests/fixtures/retrieval_eval.json"
FIXTURE = ROOT / "tests/fixtures/test_schema.sql"
LIMITS = {
    "max_calls": 4, "calls_per_case": 1, "n": 1, "message_bytes": 32768,
    "max_output_tokens": 1024, "request_seconds": 30, "case_seconds": 45,
    "batch_seconds": 180, "max_retries": 0,
}


def manifest_digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def source_hashes() -> dict[str, str]:
    paths = [DATASET, FIXTURE]
    for directory in ("app", "scripts"):
        paths.extend(p for p in (ROOT / directory).rglob("*")
                     if p.suffix in {".py", ".yml", ".yaml"})
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def runtime_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {"python": sys.version.split()[0]}
    for package in ("litellm", "sqlglot", "pydantic"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = None
    return result


def prepare(
    run_name: str, *, model: str = "deepseek-v4-flash",
    base_url: str = "https://api.deepseek.com/v1",
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", run_name):
        raise ValueError("Invalid run name")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", model):
        raise ValueError("Invalid model name")
    url = urlsplit(base_url)
    # This first profile deliberately supports only the named provider's public endpoint.
    if base_url != "https://api.deepseek.com/v1" or url.username or url.password:
        raise ValueError("Unsupported evaluation endpoint")
    dataset = read_json(DATASET)
    if not isinstance(dataset, dict):
        raise ValueError("Synthetic dataset must be an object")
    cases = dataset.get("sql_cases")
    if not isinstance(cases, list) or len(cases) != 4:
        raise ValueError("Synthetic profile requires exactly four cases")
    return {
        "profile": "synthetic-sqlite-model-v1", "provider": "deepseek", "model": model,
        "base_url": base_url, "api_key_env": "DEEPSEEK_API_KEY", "run_name": run_name,
        "limits": dict(LIMITS), "fallback_chain": [], "sources": source_hashes(),
        "runtime_versions": runtime_versions(),
        "cases": [{"id": c["id"], "question": c["question"]} for c in cases],
        "actual_cost_usd": None,
        "scope": "Synthetic public fixture only; not business accuracy or billing evidence",
    }


def validate(manifest: object, confirmation: str) -> dict[str, Any]:
    if not isinstance(manifest, dict) or manifest_digest(manifest) != confirmation:
        raise ValueError("Manifest confirmation mismatch")
    try:
        expected = prepare(manifest["run_name"], model=manifest["model"],
                           base_url=manifest["base_url"])
    except (KeyError, TypeError):
        raise ValueError("Invalid evaluation manifest") from None
    if manifest != expected:
        raise ValueError("Manifest or source files changed; prepare again")
    return expected


def run_directory(manifest: dict[str, Any]) -> Path:
    name: str = manifest["run_name"]
    return ROOT / "data/model-evaluations" / name
