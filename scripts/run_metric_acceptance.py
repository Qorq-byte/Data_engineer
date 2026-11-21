"""Verify an approved metric catalog and golden rows against a target deployment."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
import httpx2
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from app.evaluation.artifacts import protect_inputs, write_atomic
from app.evaluation.http_acceptance import bearer_headers
from app.evaluation.http_acceptance import target_origin as target_origin
from app.evaluation.metric_acceptance import load_metric_acceptance, verify_metric_acceptance

_TOKEN_ENV = "DE_METRIC_ACCEPTANCE_TOKEN"


def _mcp_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
    **_transport_options: object,
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        headers=headers, auth=auth, timeout=timeout or 15, follow_redirects=False, trust_env=False,
    )


async def run(args: argparse.Namespace) -> int:
    protect_inputs(args.output, [args.catalog, args.cases])
    acceptance = load_metric_acceptance(args.catalog, args.cases)
    origin = target_origin(args.base_url)
    mcp_url = target_origin(args.mcp_url, allow_path=True) if args.mcp_url is not None else None
    headers = bearer_headers(_TOKEN_ENV)
    mcp = None
    if mcp_url is not None:
        mcp = Client(
            StreamableHttpTransport(
                str(mcp_url), headers=headers, httpx_client_factory=_mcp_http_client,
                max_sse_event_size=2 * 1024 * 1024,
            ),
            timeout=15, init_timeout=15, cache=False,
        )
    async with httpx.AsyncClient(
        base_url=origin, headers=headers, timeout=15, trust_env=False,
    ) as client:
        report = await verify_metric_acceptance(client, acceptance, mcp=mcp)
    report["deployment_origin"] = str(origin)
    if mcp_url is not None:
        report["mcp_url"] = str(mcp_url)
    write_atomic(args.output, report)
    print(json.dumps({
        "passed": report["passed"], "check_count": len(report["checks"]),
        "cases_sha256": report["cases_sha256"],
    }))
    return 0 if report["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, epilog=f"Set {_TOKEN_ENV} before running.",
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--mcp-url", help="Also verify the deployment's Streamable HTTP endpoint")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except (OSError, ValueError, httpx.InvalidURL):
        print("Metric acceptance input is invalid or output could not be written.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
