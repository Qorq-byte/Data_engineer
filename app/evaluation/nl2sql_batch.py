"""Data-only preflight for a separate, frozen thirty-question NL2SQL profile."""

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
PROFILE = "synthetic-nl2sql-v3"
MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com/v1"
ACCEPTANCE_SHA = "ed4be1acd589350adf428491ae4db542ee686fd3bd212a3f2f3517dca6b45755"
PROMPTS = ROOT / "tests/fixtures/nl2sql_acceptance/prompt-inventory.json"
LIMITS = {"max_calls": 30, "calls_per_case": 1, "n": 1, "max_retries": 0,
          "message_bytes": 32768, "max_output_tokens": 1024, "request_seconds": 30,
          "case_seconds": 45, "worker_seconds": 1500, "cleanup_seconds": 5,
          "max_artifact_bytes": 64 * 1024 * 1024}


def load_suite() -> dict[str, Any]:
    if fingerprint(data.verify_manifest()) != ACCEPTANCE_SHA:
        raise ValueError("Frozen acceptance identity changed")
    return data.validate_sql(read_json(data.DIRECTORY / "sql.json"))


def source_paths() -> list[Path]:
    paths = data.source_paths() + [ROOT / "pyproject.toml", PROMPTS]
    for directory in ("app", "scripts"):
        paths.extend(path for path in (ROOT / directory).rglob("*")
                     if path.suffix in {".py", ".yml", ".yaml"})
    return sorted(set(paths))


def prepare(run_name: str) -> dict[str, Any]:
    if not isinstance(run_name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", run_name):
        raise ValueError("Invalid run name")
    suite = load_suite()
    prompts = read_json(PROMPTS)
    if (not isinstance(prompts, list) or len(prompts) != 30
            or [p.get("id") for p in prompts] != [c["id"] for c in suite["sql_cases"]]
            or any(set(p) != {"id", "messages_sha256", "message_bytes"}
                   or not re.fullmatch(r"[0-9a-f]{64}", p["messages_sha256"])
                   or type(p["message_bytes"]) is not int
                   or not 0 < p["message_bytes"] <= LIMITS["message_bytes"] for p in prompts)):
        raise ValueError("Invalid frozen prompt inventory")
    cases = [{key: case[key] for key in ("id", "question", "split", "tags")}
             for case in suite["sql_cases"]]
    return {"profile": PROFILE, "run_name": run_name, "provider": "deepseek", "model": MODEL,
            "base_url": BASE_URL, "api_key_env": "DEEPSEEK_API_KEY", "limits": dict(LIMITS),
            "fallback_chain": [], "cases": cases, "prompt_inventory": prompts,
            "exact_message_utf8_bytes": sum(p["message_bytes"] for p in prompts),
            "acceptance_manifest_sha256": ACCEPTANCE_SHA,
            "retrieval": "Production keyword-only SchemaRetriever; no embedding or BM25 claim",
            "workflow": "request_query: parse/link/gen/validate/exec; execute=false; audit export",
            "scenarios": list(data.SCENARIOS),
            "input_question_utf8_bytes": sum(len(c["question"].encode("utf-8")) for c in cases),
            "max_aggregate_message_bytes": 30 * LIMITS["message_bytes"],
            "max_aggregate_output_tokens_parameter": 30 * LIMITS["max_output_tokens"],
            "input_token_estimate": None, "actual_cost_usd": None,
            "runtime": {"python": platform.python_version(), **{name: version(name) for name in
                        ("litellm", "sqlglot", "pydantic", "httpx")}},
            "sources": {p.relative_to(ROOT).as_posix(): hashlib.sha256(
                p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for p in source_paths()},
            "resource_scope": "Parent supervises worker1500s plus cleanup5s. Artifact64MiB "
                              "checked after cases, not a filesystem quota. No provider memory "
                              "or dollar quota; termination cannot guarantee billing reversal.",
            "scope": "Public synthetic30 cases x3 local scenarios; not business accuracy. "
                     "Alias is not an immutable provider model version; no score-driven tuning."}


def manifest_digest(value: object) -> str:
    return fingerprint(value)


def validate(value: object, confirmation: str) -> dict[str, Any]:
    if not isinstance(value, dict) or manifest_digest(value) != confirmation:
        raise ValueError("Explicit matching manifest SHA required")
    name = value.get("run_name")
    if not isinstance(name, str):
        raise ValueError("Invalid run name")
    expected = prepare(name)
    if manifest_digest(expected) != manifest_digest(value):
        raise ValueError("Manifest, source, inputs or runtime changed")
    return expected


def run_directory(name: str) -> Path:
    root = (ROOT / "data/nl2sql-acceptance").resolve()
    target = (root / name).resolve()
    if target.parent != root or not target.is_relative_to(ROOT.resolve()):
        raise ValueError("Invalid run directory")
    return target
