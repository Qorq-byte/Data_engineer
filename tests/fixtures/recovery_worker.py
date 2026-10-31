"""Subprocess probe: start the real lifespan against a state root and report readiness.

Used by the recovery tests so that "restart" means an actual new process, not a
re-initialised in-process singleton.

    python -m tests.fixtures.recovery_worker --state-root DIR --mode status
    python -m tests.fixtures.recovery_worker --state-root DIR --mode index --documents 5
    python -m tests.fixtures.recovery_worker --state-root DIR --mode index-crash
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
from pathlib import Path


def configure(state_root: Path, audit_path: str) -> None:
    os.environ.update({
        "LLM_MOCK_MODE": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "RAG_EMBEDDING_PROVIDER": "mock",
        "RAG_LANCEDB_DIR": str(state_root / "lancedb"),
        "RAG_BM25_PATH": str(state_root / "bm25.db"),
        "LLM_AUDIT_PATH": audit_path,
        "JWT_SECRET": "recovery-probe-secret-value-0123456789",
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_PORT": "1",
        "APP_ENV": "test",
    })


async def _index(documents: int) -> None:
    from app.models.rag_schema import SchemaDocument
    from app.rag.engine import get_rag_engine

    rag = get_rag_engine().schema_rag
    await rag.index_schemas([
        SchemaDocument(doc_id=f"probe-{index}", db_id="probe", schema_name="main",
                       table_name=f"table_{index}",
                       embedding_text=f"table table_{index} columns id, amount")
        for index in range(documents)
    ], replace_db_id="probe")


async def run(args: argparse.Namespace) -> int:
    import httpx

    from app.api import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        if args.mode == "index":
            await _index(args.documents)
        elif args.mode == "index-crash":
            threading.Timer(0.02, lambda: os._exit(9)).start()
            await _index(args.documents)
            os._exit(9)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://probe") as client:
            response = await client.get("/health/ready")
            payload = response.json()
        print(json.dumps({
            "mode": args.mode,
            "http_status": response.status_code,
            "components": getattr(app.state, "components", {}),
            "rag": payload.get("rag"),
            "status": payload.get("status"),
            "index_counts": _index_counts(),
        }, ensure_ascii=False))
    return 0


def _index_counts() -> dict[str, int] | None:
    try:
        from app.rag.engine import get_rag_engine

        return get_rag_engine().schema_rag.stats()
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--audit-path", default="")
    parser.add_argument("--mode", default="status",
                        choices=("status", "index", "index-crash"))
    parser.add_argument("--documents", type=int, default=5)
    args = parser.parse_args(argv)
    state_root = args.state_root.resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    configure(state_root, args.audit_path)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
