"""Prepare or explicitly execute one supervised thirty-case synthetic model batch."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from app.evaluation.artifacts import protect_inputs, read_json
from app.evaluation.nl2sql_batch import (
    ROOT,
    manifest_digest,
    prepare,
    run_directory,
    source_paths,
    validate,
)


def failure(directory: Path, manifest: dict[str, Any], code: str) -> dict[str, Any]:
    from app.evaluation.acceptance_sql import evaluate_sql
    from app.evaluation.embedding_runtime import write_atomic
    from app.evaluation.nl2sql_batch import load_suite

    report = {"status": "failed", "manifest_sha256": manifest_digest(manifest),
              "error_code": code, "case_count": 30, "physical_calls_reserved": None,
              "actual_cost_usd": None, "evaluation": evaluate_sql(load_suite(), []),
              "missing_reason": "No finalized audit-bound export; partial responses not scored"}
    try:
        rows = read_json(directory / "requests.json")
        if isinstance(rows, list):
            report["physical_calls_reserved"] = len(rows)
    except Exception:
        pass
    write_atomic(directory / "report.json", report)
    state = read_json(directory / "run-state.json")
    if isinstance(state, dict):
        write_atomic(directory / "run-state.json", {**state, "state": "failed"})
    return report


def supervise(manifest: dict[str, Any], key: str) -> dict[str, Any]:
    from app.evaluation.embedding_runtime import write_atomic

    manifest = validate(manifest, manifest_digest(manifest))
    directory = run_directory(manifest["run_name"])
    directory.mkdir(parents=True, exist_ok=False)
    owner = uuid.uuid4().hex
    digest = manifest_digest(manifest)
    write_atomic(directory / "manifest.json", manifest)
    write_atomic(directory / "run-state.json", {"owner": owner, "state": "starting",
                                                "manifest_sha256": digest})
    workspace = directory / "workspace"
    workspace.mkdir()
    keep = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
        "USERNAME", "USER", "LOGNAME", "LNAME", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
    environment = dict(keep, PYTHONPATH=str(ROOT), PYTHONUTF8="1", LLM_MOCK_MODE="0",
        LITELLM_LOCAL_MODEL_COST_MAP="True", RAG_EMBEDDING_PROVIDER="mock", DEEPSEEK_API_KEY=key,
        LLM_AUDIT_PATH=str(directory / "usage.sqlite3"), LLM_AUDIT_CANDIDATE_DIGESTS="true",
        DE_TRACE_DIR=str(directory / "traces"))
    command = [sys.executable, "-m", "scripts.run_nl2sql_acceptance", "--worker",
        "--manifest", str(directory / "manifest.json"), "--confirm-manifest-sha256", digest,
        "--owner-token", owner]
    with (directory / "worker.log").open("w", encoding="utf-8") as log:
        try:
            child = subprocess.Popen(command, cwd=workspace, env=environment, stdout=log,
                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            return failure(directory, manifest, "WORKER_START_FAILED")
        try:
            code = child.wait(timeout=manifest["limits"]["worker_seconds"])
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            child.kill()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return failure(directory, manifest, "WORKER_CLEANUP_FAILED")
            return failure(directory, manifest, "WORKER_DEADLINE_OR_INTERRUPTION")
    try:
        report = read_json(directory / "report.json")
        if (code == 0 and isinstance(report, dict) and report.get("status") == "finished"
                and report.get("manifest_sha256") == digest
                and report.get("evaluation", {}).get("case_count") == 30
                and report["evaluation"].get("check_count") == 90
                and type(report.get("physical_calls_reserved")) is int
                and 0 <= report["physical_calls_reserved"] <= 30
                and type(report.get("native_budget_calls")) is int
                and 0 <= report["native_budget_calls"] <= report["physical_calls_reserved"]):
            return report
    except Exception:
        pass
    return failure(directory, manifest, "WORKER_FAILED")


async def worker(manifest: dict[str, Any], owner: str) -> dict[str, Any]:
    from app.evaluation.embedding_runtime import write_atomic

    directory = run_directory(manifest["run_name"])
    state = read_json(directory / "run-state.json")
    if not owner or len(owner) != 32 or state != {"owner": owner, "state": "starting",
                                                 "manifest_sha256": manifest_digest(manifest)}:
        raise ValueError("Worker ownership invalid")
    write_atomic(directory / "run-state.json", {**state, "state": "running"})
    try:
        with open(os.devnull, "w", encoding="utf-8") as sink, \
                contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            import litellm

            from app.evaluation.nl2sql_runtime import run_batch

            litellm.telemetry = False
            try:
                report = await run_batch(manifest, os.environ["DEEPSEEK_API_KEY"], directory)
            finally:
                cleanup = getattr(litellm, "close_litellm_async_clients", None)
                if cleanup is not None:
                    await cleanup()
        write_atomic(directory / "run-state.json", {**state, "state": report["status"]})
        return report
    except BaseException:
        failure(directory, manifest, "WORKER_FAILED")
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-name", default="stage44-sql-v3")
    parser.add_argument("--confirm-manifest-sha256", default="")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--owner-token", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    previous = logging.root.manager.disable
    logging.disable(max(previous, logging.CRITICAL))
    try:
        if args.prepare:
            if args.env_file or args.owner_token or args.confirm_manifest_sha256:
                raise ValueError("Invalid preparation arguments")
            from app.evaluation.artifacts import write_atomic

            protect_inputs(args.manifest, source_paths())
            value = prepare(args.run_name)
            if args.manifest.resolve().is_relative_to(run_directory(args.run_name)):
                raise ValueError("Manifest must be outside run directory")
            write_atomic(args.manifest, value)
            print(json.dumps({"status": "prepared", "manifest_sha256": manifest_digest(value),
                              "limits": value["limits"]}))
            return 0
        value = validate(read_json(args.manifest), args.confirm_manifest_sha256)
        if args.worker:
            if args.env_file:
                raise ValueError("Worker must not load host env file")
            result = asyncio.run(worker(value, args.owner_token))
        else:
            if args.owner_token:
                raise ValueError("Invalid ownership arguments")
            from dotenv import dotenv_values

            env = dotenv_values(args.env_file) if args.env_file else {}
            key = os.getenv("DEEPSEEK_API_KEY") or env.get("DEEPSEEK_API_KEY")
            if not key:
                raise ValueError("Selected provider credential missing")
            if any(str(v).lower() in {"1", "true", "yes"} for v in
                   (os.getenv("LLM_MOCK_MODE", ""), env.get("LLM_MOCK_MODE", ""))):
                raise ValueError("Live run cannot use mock mode")
            result = supervise(value, key)
        print(json.dumps({"status": result["status"], "actual_cost_usd": None}))
        return 0 if result["status"] == "finished" else 2
    except (Exception, KeyboardInterrupt):
        print("NL2SQL acceptance stopped; inspect private run state. No automatic retry.",
              file=sys.stderr)
        return 2
    finally:
        logging.disable(previous)


if __name__ == "__main__":
    raise SystemExit(main())
