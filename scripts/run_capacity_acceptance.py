"""Measure authenticated HTTP query capacity against a frozen preproduction workload."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import httpx

from app.evaluation.artifacts import protect_inputs, write_atomic
from app.evaluation.capacity_acceptance import PROFILE, load_capacity_plan, run_capacity_plan
from app.evaluation.http_acceptance import bearer_headers, target_origin

_TOKEN_ENV = "DE_CAPACITY_ACCEPTANCE_TOKEN"


async def run(args: argparse.Namespace) -> int:
    protect_inputs(args.output, [args.manifest])
    plan = load_capacity_plan(args.manifest)
    origin = target_origin(args.base_url)
    credentials = {
        tenant: bearer_headers(_TOKEN_ENV if plan.profile == PROFILE else f"{_TOKEN_ENV}_{tenant}")
        for tenant in plan.tenant_ids
    }
    concurrency = max(phase.concurrency for phase in plan.phases)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with AsyncExitStack() as stack:
        clients = {
            tenant: await stack.enter_async_context(httpx.AsyncClient(
                base_url=origin, headers=headers, timeout=None, trust_env=False, limits=limits,
            ))
            for tenant, headers in credentials.items()
        }
        report = await run_capacity_plan(clients, plan)
    report["deployment_origin"] = str(origin)
    write_atomic(args.output, report)
    print(json.dumps({"passed": report["passed"], "manifest_sha256": plan.sha256,
                      "phase_count": len(report["phases"])}))
    return 0 if report["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, epilog=f"Set {_TOKEN_ENV} for v1 or {_TOKEN_ENV}_<tenant_id> for v2.",
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    try:
        return asyncio.run(run(parser.parse_args(argv)))
    except (OSError, ValueError, httpx.InvalidURL):
        print("Capacity acceptance invalid input or output failure.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
