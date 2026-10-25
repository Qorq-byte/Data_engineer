"""Prepare a public manifest, or explicitly execute one isolated four-case model batch."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import Any

from app.evaluation.artifacts import protect_inputs, read_json, write_atomic
from app.evaluation.model_batch import (
    PROFILES,
    V1_PROFILE,
    manifest_digest,
    prepare,
    profile_inputs,
    run_directory,
    validate,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--run-name")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--profile", choices=PROFILES)
    parser.add_argument("--confirm-manifest-sha256", default="")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    phase = "preflight"
    directory: Path | None = None
    try:
        if args.prepare:
            manifest = prepare(args.run_name or "synthetic-smoke", model=args.model,
                               profile=args.profile or V1_PROFILE)
            protect_inputs(args.manifest, profile_inputs(manifest["profile"]))
            if args.manifest.resolve().is_relative_to(run_directory(manifest).resolve()):
                raise ValueError("Manifest must be outside the disposable run directory")
            write_atomic(args.manifest, manifest)
            print(f"Prepared four-case batch; manifest_sha256={manifest_digest(manifest)}")
            return 0
        manifest = validate(read_json(args.manifest), args.confirm_manifest_sha256)
        if args.profile is not None and args.profile != manifest["profile"]:
            raise ValueError("Execution profile does not match manifest")
        # Read one selected credential only. Never load the host .env into the application.
        from dotenv import dotenv_values

        values = dotenv_values(args.env_file) if args.env_file else {}
        if any(str(v).lower() in {"1", "true", "yes"} for v in
               (os.getenv("LLM_MOCK_MODE", ""), values.get("LLM_MOCK_MODE", ""))):
            raise ValueError("Mock mode is incompatible with live evaluation")
        key = os.getenv(manifest["api_key_env"]) or values.get(manifest["api_key_env"])
        if not key:
            raise ValueError("Selected provider credential is missing")
        target = run_directory(manifest).resolve()
        target.mkdir(parents=True, exist_ok=False)
        directory = target
        workspace = directory / "workspace"
        workspace.mkdir()
        write_atomic(directory / "manifest.json", manifest)
        # Dedicated CLI process: application settings inherit no host business connections.
        keep = {k: v for k, v in os.environ.items() if k.upper() in {
            "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
            "USERNAME", "USER", "LOGNAME", "LNAME",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "PYTHONUTF8",
        }}
        os.environ.clear()
        os.environ.update(keep)
        os.environ.update({
            "LLM_MOCK_MODE": "0", "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            "RAG_EMBEDDING_PROVIDER": "mock", "LLM_AUDIT_PATH": str(directory / "usage.sqlite3"),
            "LLM_AUDIT_CANDIDATE_DIGESTS": "true", "DE_TRACE_DIR": str(directory / "traces"),
        })
        os.chdir(workspace)
        logging.disable(logging.CRITICAL)
        # Some SDKs print raw provider exceptions; these must not reach the terminal/logs.
        with open(os.devnull, "w", encoding="utf-8") as sink, \
                contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            phase = "runtime_import"
            from app.evaluation.model_runtime import run_batch
            from app.llm.router import _LITELLM_AVAILABLE

            if not _LITELLM_AVAILABLE:
                raise ValueError("Model SDK is unavailable")
            async def worker() -> dict[str, Any]:
                import litellm

                litellm.telemetry = False
                try:
                    return await run_batch(manifest, key, directory)
                finally:
                    cleanup = getattr(litellm, "close_litellm_async_clients", None)
                    if cleanup is not None:
                        await cleanup()

            phase = "worker_start"
            report = asyncio.run(worker())
        print(f"Batch {report['status']}; reserved_calls={report['physical_calls_reserved']}; "
              "actual_cost_usd=unknown; inspect the private run report")
        return 0 if report["status"] == "finished" else 1
    except (Exception, KeyboardInterrupt):
        if directory is not None and not (directory / "report.json").exists():
            with contextlib.suppress(OSError):
                write_atomic(directory / "report.json", {
                    "status": "startup_failed", "phase": phase,
                    "physical_calls_reserved": None, "actual_cost_usd": None,
                })
        print("Controlled evaluation stopped; no automatic retry. Inspect the private run report.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
