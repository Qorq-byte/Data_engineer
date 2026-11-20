"""Independent SDK client/server processes, real protocol streams and SQLite."""

import asyncio
import json
import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from tests.test_mcp.test_metric_tools import args, call

ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def stdio_host(database, directory, *, trust):
    catalog = directory / "catalog.yml"
    catalog.write_bytes((ROOT / "tests/fixtures/metric_catalog.yml").read_bytes())
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "USERNAME", "USER",
    }}
    environment.update(
        PYTHONPATH=str(ROOT), PYTHONUTF8="1", LLM_MOCK_MODE="1",
        LITELLM_LOCAL_MODEL_COST_MAP="True", LLM_AUDIT_PATH="", SHARED_WORKER_RELEASE="",
        JWT_SECRET="stdio-fixture-secret-never-used-outside-tests",
        METRIC_CATALOG_PATH=str(catalog), AGENT_CONFIG_PATH=str(ROOT / "app/config/agent.yml"),
        DE_TRACE_DIR=str(directory / "traces"), DE_PLAN_HISTORY=str(directory / "plans.sqlite3"),
    )
    command = ["-m", "tests.fixtures.metric_stdio_worker", "--database", database]
    if trust:
        command.append("--trust-local")
    transport = StdioTransport(
        command=sys.executable, args=command, env=environment, cwd=str(directory),
        keep_alive=False, log_file=directory / "stderr.txt",
    )
    # A fresh process imports the complete query runtime on its first tool call.
    # This is a bounded correctness test, not a warmed-up latency benchmark.
    async with Client(transport, timeout=45) as client:
        assert json.loads((directory / "started.json").read_text())["pid"] != os.getpid()
        yield client, catalog
    async with asyncio.timeout(5):
        while not (directory / "closed.json").exists():
            await asyncio.sleep(0.02)
    assert (directory / "started.json").read_bytes() == (directory / "closed.json").read_bytes()


@pytest.mark.parametrize("trust", [False, True])
async def test_stdio_metric_tools_require_explicit_process_trust(test_sqlite_db, tmp_path, trust):
    async with stdio_host(test_sqlite_db, tmp_path, trust=trust) as (client, _):
        tools = await client.list_tools()
        assert {"list_metrics", "metric_query"} <= {tool.name for tool in tools}
        catalog = await call(client, "list_metrics", {"domain": "ecommerce", "db_id": "metric-db"})
        result = await call(client, "metric_query", args(execute=True))
        if not trust:
            assert catalog["http_status"] == result["http_status"] == 401
            assert catalog["status"] == result["status"] == "failed"
            assert not list((tmp_path / "traces").glob("*.jsonl"))
            return
        assert catalog["status"] == result["status"] == "completed"
        assert catalog["version"] == "synthetic-ecommerce-v1"
        with sqlite3.connect(test_sqlite_db) as connection:
            expected = [list(row) for row in connection.execute(
                "SELECT SUM(amount), COUNT(*) FROM orders"
            )]
        assert result["rows"] == expected and result["llm_calls"] == 0
        assert result["plan_output"]["node_names"] == [
            "metric_resolve", "validate_sql", "execute_sql",
        ]
        assert result["plan_output"]["metric_catalog"]["sha256"] == catalog["sha256"]
        validation = await call(client, "metric_query", args())
        assert validation["status"] == "completed" and validation["executed"] is False
        assert validation["validation"]["passed"] is True
        invalid = await call(client, "metric_query", args(db_id="other"))
        assert invalid["http_status"] == 400 and invalid["rows"] == []


async def test_stdio_catalog_change_rejects_old_client_version(test_sqlite_db, tmp_path):
    async with stdio_host(test_sqlite_db, tmp_path, trust=True) as (client, path):
        old = await call(client, "list_metrics", {"domain": "ecommerce", "db_id": "metric-db"})
        path.write_text(path.read_text(encoding="utf-8").replace(
            "synthetic-ecommerce-v1", "synthetic-ecommerce-v2"
        ), encoding="utf-8")
        rejected = await call(client, "metric_query", args(metric_catalog_version=old["version"]))
        assert rejected["status"] == "failed" and rejected["http_status"] == 400
        assert not list((tmp_path / "traces").glob("*.jsonl"))
        current = await call(client, "list_metrics", {"domain": "ecommerce", "db_id": "metric-db"})
        assert old["sha256"] != current["sha256"]
        result = await call(client, "metric_query", args(
            metric_catalog_version=current["version"], execute=True,
        ))
        assert result["status"] == "completed" and result["llm_calls"] == 0
