"""Local synthetic browser fixture; never start as a production server.

Run: python -m scripts.browser_qa_server
Only 127.0.0.1:8018, ephemeral JWT, in-memory SQLite, scripted model, no auth MySQL.
Generated test token is written only into ignored .verify_tmp_scripts/browser_auth.js.
"""

import asyncio
import json
import os
import secrets
from pathlib import Path

os.environ["JWT_SECRET"] = secrets.token_urlsafe(48)
os.environ["LLM_MOCK_MODE"] = "1"
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["RAG_EMBEDDING_PROVIDER"] = "mock"
os.environ["DE_TRACE_DIR"] = ".verify_tmp_scripts/traces"


async def main():
    import uvicorn

    from app.api import create_app, query
    from app.auth.security import create_access_token
    from app.config.settings import settings
    from app.db.connections import ConnectionFactory, DatabaseType
    from app.llm.router import LiteLLMRouter, RouterConfig

    class Router(LiteLLMRouter):
        async def complete(self, messages, **kwargs):
            text = str(messages)
            if "slow_case" in text:
                await asyncio.sleep(5)
            sql = "SELECT id FROM orders ORDER BY id"
            if "empty_case" in text:
                sql = "SELECT id FROM orders WHERE id < 0"
            if "error_case" in text:
                sql = "DELETE FROM orders"
            return {"choices": [{"message": {"content": sql}}]}

    router = Router(RouterConfig(), mock_mode=True)
    query.get_default_router = lambda: router
    conn = await ConnectionFactory.create(DatabaseType.SQLITE, {}, "qa-fixture")
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY)")
    conn.executemany("INSERT INTO orders VALUES (?)", [(1,), (2,), (3,)])
    conn.commit()
    Path(".verify_tmp_scripts").mkdir(exist_ok=True)
    token = create_access_token(settings.admin_user_ids[0], "synthetic-browser-qa")
    Path(".verify_tmp_scripts/browser_auth.js").write_text(
        'localStorage.setItem("nl2sql_token", ' + json.dumps(token) + ");\n"
        'localStorage.setItem("nl2sql_user", JSON.stringify({username:"合成测试", id:1}));',
        encoding="utf-8",
    )
    app = create_app()
    counts = {}

    @app.middleware("http")
    async def count(request, call_next):
        path = request.url.path
        counts[path] = counts.get(path, 0) + 1
        return await call_next(request)

    @app.get("/qa-counts")
    async def qa_counts():
        return counts

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8018, lifespan="off"))
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
