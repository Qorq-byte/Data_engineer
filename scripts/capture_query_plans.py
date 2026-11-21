"""Collect query responses from an explicitly selected preproduction service."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import httpx

from app.evaluation.acceptance_data import business_source_paths, load_business_dataset
from app.evaluation.artifacts import protect_inputs, write_atomic
from app.evaluation.http_acceptance import bearer_headers, target_origin
from app.evaluation.plan_capture import CaptureSettings, capture_query_plans
from app.evaluation.plan_comparison import TRIAL_PROFILES

_TOKEN_ENV = "DE_PLAN_CAPTURE_TOKEN"


async def run(args: argparse.Namespace) -> int:
    protect_inputs(args.output, business_source_paths(args.dataset_manifest))
    dataset = load_business_dataset(args.dataset_manifest)
    origin = target_origin(args.base_url)
    headers = bearer_headers(_TOKEN_ENV)
    settings = CaptureSettings(
        args.tenant_id, args.split, args.configuration_sha256, args.max_requests,
        args.timeout_seconds,
    )
    async with httpx.AsyncClient(
        base_url=origin, headers=headers, timeout=None, trust_env=False,
    ) as client:
        bundle = await capture_query_plans(
            client, dataset, settings, baseline_only=args.baseline_only, trial_plan=args.trial_plan,
        )
    acquisition = bundle["acquisition"]
    acquisition["deployment_origin"] = str(origin)
    write_atomic(args.output, bundle, indent=None)
    print(json.dumps({
        "status": acquisition["status"],
        "attempted_requests": acquisition["attempted_requests"],
        "stop_reason": acquisition["stop_reason"],
    }))
    return 0 if acquisition["status"] == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, epilog=f"Set {_TOKEN_ENV} before running.",
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tenant-id", required=True, type=int)
    parser.add_argument("--dataset-manifest", required=True, type=Path)
    parser.add_argument("--configuration-sha256", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--baseline-only", action="store_true", help="Capture only request_query")
    mode.add_argument("--trial-plan", choices=TRIAL_PROFILES, default="reflection")
    parser.add_argument("--split", choices=("dev", "holdout"), default="holdout")
    parser.add_argument("--max-requests", required=True, type=int)
    parser.add_argument("--timeout-seconds", type=float, default=60)
    parser.add_argument("--output", required=True, type=Path)
    try:
        return asyncio.run(run(parser.parse_args(argv)))
    except (OSError, ValueError, sqlite3.Error, RuntimeError, httpx.InvalidURL):
        print("Plan capture failed; check its inputs and report destination.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
