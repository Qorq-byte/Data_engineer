"""Owned-service probe for a committed settings write followed by process death."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

KEY = "stage57-synthetic-provider-key"
SECRET = "stage57-test-signing-secret-never-used-outside-fixtures"


def candidate() -> dict:
    from app.security.crypto import encrypt_secret

    return {
        "default_model": "stage57-model", "fallback_model": "", "max_tokens": 777,
        "providers": {"fixture": {
            "enabled": True, "models": ["stage57-model"],
            "api_key_enc": encrypt_secret(KEY), "api_key_configured": True,
            "base_url": "https://example.invalid/v1",
        }},
    }


async def run(mode: str) -> None:
    import httpx

    from app.api import create_app
    from app.api import settings as api_settings
    from app.db.connections import ConnectionFactory, DatabaseType
    from app.llm import factory
    from app.llm.router import LiteLLMRouter, RouterConfig
    from app.rag import engine
    from app.storage.mysql_store import MySQLStore

    async def forbidden(*args, **kwargs):
        raise AssertionError("Model requests forbidden in settings recovery probe")

    LiteLLMRouter._call_provider = forbidden
    # Synthetic provider only; constructing a non-mock router does not call it.
    factory._mock_forced = lambda: False
    factory.publish_llm_router(LiteLLMRouter(RouterConfig(default_model="old"), mock_mode=False))
    if mode == "commit-crash":
        store = await MySQLStore.create()

        def crash_before_publish(prepared):
            Path("before-publish.json").write_text(json.dumps({
                "model": factory.get_default_router().config.default_model,
                "memory_model": api_settings._settings_store.get("llm", {}).get("default_model"),
            }), encoding="utf-8")
            os._exit(57)

        factory.publish_llm_router = crash_before_publish
        await api_settings._commit_llm_configuration(candidate(), store)
        raise AssertionError("Expected process termination before publish")

    # RAG is optional to execution readiness and is outside this probe's scope.
    async def unavailable_rag():
        raise RuntimeError("RAG deliberately disabled for settings probe")

    engine.initialize_rag_engine = unavailable_rag
    await ConnectionFactory.create(DatabaseType.SQLITE, {"database": ":memory:"}, "probe")
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://fixture") as client:
            response = await client.get("/health/ready")
        router = factory.get_default_router()
        result = {
            "pid": os.getpid(), "http_status": response.status_code,
            "readiness": response.json(), "model": router.config.default_model,
            "max_tokens": router.config.max_output_tokens,
            "key_restored": bool(router.config.providers) and
                router.config.providers["fixture"].api_key == KEY,
        }
        Path("restored.json").write_text(json.dumps(result), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("commit-crash", "restore"))
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port == 3306:
        raise ValueError("Dedicated fixture port required")
    if not re.fullmatch(r"de_settings57_[0-9a-f]{32}", args.database):
        raise ValueError("Owned fixture database required")
    # Set before any app import. The parent also gives this process a clean cwd.
    for prefix in ("MYSQL", "AUTH_MYSQL"):
        os.environ.update({f"{prefix}_HOST": "127.0.0.1", f"{prefix}_PORT": str(args.port),
                           f"{prefix}_USER": "root", f"{prefix}_PASSWORD":
                           "stage40-disposable-admin", f"{prefix}_DATABASE": args.database})
    os.environ.update({"JWT_SECRET": SECRET, "LLM_MOCK_MODE": "1",
                       "LITELLM_LOCAL_MODEL_COST_MAP": "True", "RAG_EMBEDDING_PROVIDER": "mock"})
    asyncio.run(run(args.mode))


if __name__ == "__main__":
    main()
