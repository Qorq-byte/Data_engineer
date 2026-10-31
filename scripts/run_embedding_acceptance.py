"""Prepare an offline manifest or explicitly execute one bounded local embedding run."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from app.evaluation.artifacts import protect_inputs, read_json, write_atomic
from app.evaluation.embedding_batch import (
    manifest_digest,
    prepare,
    read_model_identity,
    source_paths,
    validate,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path)
    parser.add_argument("--run-name", default="stage43-embeddinggemma-v3")
    parser.add_argument("--confirm-manifest-sha256")
    parser.add_argument("--owner-token", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    previous = logging.root.manager.disable
    logging.disable(max(previous, logging.CRITICAL))
    try:
        if args.prepare:
            if (args.model_manifest is None or args.confirm_manifest_sha256 is not None
                    or args.owner_token is not None):
                raise ValueError("Invalid preparation arguments")
            protect_inputs(args.manifest, source_paths() + [args.model_manifest])
            result = prepare(args.run_name, read_model_identity(args.model_manifest))
            write_atomic(args.manifest, result)
            print(json.dumps({"status": "prepared", "manifest_sha256": manifest_digest(result),
                              "model": result["model"], "limits": result["limits"],
                              "resource_scope": result["resource_scope"]}))
            return 0
        if args.model_manifest is not None or not args.confirm_manifest_sha256:
            raise ValueError("Explicit manifest confirmation required")
        result = validate(read_json(args.manifest), args.confirm_manifest_sha256)
        # Import runtime/providers only after complete manifest/source validation.
        if args.worker:
            if not args.owner_token:
                raise ValueError("Worker ownership required")
            from app.evaluation.embedding_runtime import worker

            report = asyncio.run(worker(result, args.confirm_manifest_sha256, args.owner_token))
        else:
            if args.owner_token is not None:
                raise ValueError("Invalid execution ownership arguments")
            from app.evaluation.embedding_runtime import supervise

            report = supervise(result, args.confirm_manifest_sha256)
        print(json.dumps({"status": report["status"],
                          "manifest_sha256": args.confirm_manifest_sha256,
                          "actual_cost_usd": None}))
        return 0 if report["status"] == "completed" else 2
    except Exception:
        print("Embedding acceptance failed; inspect owned run state. No automatic retry.",
              file=sys.stderr)
        return 2
    finally:
        logging.disable(previous)


if __name__ == "__main__":
    raise SystemExit(main())
