"""Loopback HTTP fixture with real MySQL state and a transport-boundary model stub."""

import argparse
import asyncio
import json
import os
import re
import socket
from pathlib import Path

SECRET = "shared-query-fixture-signing-secret-never-used-outside-tests"
ROOT = Path(__file__).resolve().parents[2]


async def run(args):
    import aiomysql
    import uvicorn
    from fastapi import Depends

    from app.api import create_app
    from app.auth.access import require_operator
    from app.db.connections import ConnectionFactory, DatabaseType
    from app.harness.mysql_coordination import MySQLCoordinator
    from app.harness.shared_query_runtime import SharedQueryRuntime, publish_shared_runtime
    from app.llm import factory
    from app.llm.router import LiteLLMRouter
    from app.rag import engine
    from app.storage.mysql_store import MySQLStore
    from app.storage.shared_sessions import MySQLSessionStore

    # Only the provider boundary is scripted; plan, permissions, SQL execution,
    # authentication, storage and inter-process coordination remain real.
    calls = 0

    async def model_boundary(self, provider, model, messages, *positional, **kwargs):
        nonlocal calls
        calls += 1
        Path("model-calls.txt").write_text(str(calls), encoding="utf-8")
        if Path("block-model").exists():
            Path("model-entered").touch()
            try:
                async with asyncio.timeout(20):
                    while not Path("release-model").exists():
                        await asyncio.sleep(0.02)
            finally:
                Path("model-cleaned-up").touch()
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "SELECT id FROM orders ORDER BY id"},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    LiteLLMRouter._call_provider = model_boundary
    factory._mock_forced = lambda: False

    async def unavailable_rag():
        raise RuntimeError("Shared query fixture explicitly uses keyword retrieval")

    immutable = bool(os.environ.get("SHARED_WORKER_RELEASE"))
    if not immutable:
        engine.initialize_rag_engine = unavailable_rag
    store = await MySQLStore.create()
    await store.init_tables()
    coordination_pool = await aiomysql.create_pool(
        host="127.0.0.1",
        port=args.port,
        user="root",
        password="stage40-disposable-admin",
        db=args.database,
        autocommit=True,
        minsize=1,
        maxsize=4,
    )
    if not immutable:
        runtime = SharedQueryRuntime(
            MySQLCoordinator(coordination_pool, args.database, 1),
            MySQLSessionStore(store._pool, args.database, max_entries=20, ttl_seconds=60),
        )
        await runtime.initialize()
        publish_shared_runtime(runtime)
        await ConnectionFactory.create(
            DatabaseType.MYSQL,
            {
                "host": "127.0.0.1",
                "port": args.port,
                "user": "stage40_reader",
                "password": "stage40-disposable-reader",
                "database": "de_stage40_fixture",
            },
            "fixture",
        )
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))

    @app.get("/probe/local-sessions", dependencies=[Depends(require_operator)])
    async def local_sessions():
        from app.api.query import _SESSION_STORE

        return {"count": len(_SESSION_STORE)}

    @app.post("/probe/stop", dependencies=[Depends(require_operator)])
    async def stop():
        server.should_exit = True
        return {"stopping": True}

    @app.post("/probe/mcp", dependencies=[Depends(require_operator)])
    async def mcp():
        from fastmcp import Client

        from app.mcp.fastmcp_server import create_nl2sql_server

        async with Client(create_nl2sql_server()) as client:
            result = await client.call_tool(
                "nl_query",
                {
                    "nl_text": "find order ids",
                    "db_id": "fixture",
                },
            )
            return result.structured_content

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        task = asyncio.create_task(server.serve(sockets=[listener]))
        async with asyncio.timeout(20):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Fixture server did not start")
                await asyncio.sleep(0.02)
        Path("ready.pending").write_text(json.dumps({"port": port}), encoding="utf-8")
        Path("ready.pending").replace("ready.json")
        await task
    finally:
        publish_shared_runtime(None)
        coordination_pool.close()
        await coordination_pool.wait_closed()
        listener.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port == 3306:
        raise ValueError("Dedicated MySQL fixture port required")
    if not re.fullmatch(r"de_coord66_[0-9a-f]{32}", args.database):
        raise ValueError("Owned fixture schema required")
    for prefix in ("MYSQL", "AUTH_MYSQL"):
        os.environ.update(
            {
                f"{prefix}_HOST": "127.0.0.1",
                f"{prefix}_PORT": str(args.port),
                f"{prefix}_USER": "root",
                f"{prefix}_PASSWORD": "stage40-disposable-admin",
                f"{prefix}_DATABASE": args.database,
            }
        )
    os.environ.update(
        JWT_SECRET=SECRET,
        LLM_MOCK_MODE="1",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        RAG_EMBEDDING_PROVIDER="mock",
        AGENT_CONFIG_PATH=str(ROOT / "app/config/agent.yml"),
    )
    os.environ.setdefault("LLM_AUDIT_PATH", str(Path.cwd() / "usage-audit.sqlite3"))
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
