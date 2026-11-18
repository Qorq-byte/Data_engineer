"""Explicit temporary SQLite host for real stdio protocol acceptance only."""

import argparse
import asyncio
import json
import os
from pathlib import Path


async def run(args):
    from app.db.connections import ConnectionFactory, DatabaseType
    from app.mcp.fastmcp_server import create_nl2sql_server

    database = Path(args.database).resolve()
    if not database.is_file() or not database.is_relative_to(Path.cwd().resolve()):
        raise ValueError("Temporary fixture database in the working directory required")
    await ConnectionFactory.create(DatabaseType.SQLITE, {"path": str(database)}, "metric-db")
    Path("started.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    try:
        server = create_nl2sql_server(trust_local_metric_tools=args.trust_local)
        await server.run_async(transport="stdio", show_banner=False)
    finally:
        await ConnectionFactory.aclose_all()
        Path("closed.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--trust-local", action="store_true")
    asyncio.run(run(parser.parse_args()))
