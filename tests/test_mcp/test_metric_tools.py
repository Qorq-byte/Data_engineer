"""Real MCP/ASGI and SQLite checks for scoped catalog discovery and execution."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest

from app.auth.security import create_access_token
from app.config.settings import settings
from app.db.connections import ConnectionFactory
from app.mcp.fastmcp_server import create_nl2sql_server
from tests.test_api.test_metric_query import body
from tests.test_api.test_metric_query import metric_env as metric_env


def args(**overrides):
    values = {
        "domain": "ecommerce", "db_id": "metric-db", "metrics": ["revenue", "order_count"],
        "metric_catalog_version": "synthetic-ecommerce-v1",
    }
    values.update(overrides)
    return values


async def call(server, name, arguments):
    return (await server.call_tool(name, arguments)).structured_content


async def http_call(server, name, arguments, token=None):
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=headers,
    ) as client:
        response = await client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
    assert response.status_code == 200, response.text
    return response.json()["result"]["structuredContent"]


async def test_discover_and_query_match_rest_without_provider_calls(metric_env):
    client, model, *_ = metric_env
    server = create_nl2sql_server(trust_local_metric_tools=True)
    catalog = await call(server, "list_metrics", {"domain": "ecommerce", "db_id": "metric-db"})
    rest = client.get(
        "/api/v1/metrics", params={"domain": "ecommerce", "database": "metric-db"}
    ).json()
    assert catalog == {"status": "completed", **rest}
    values = args(
        execute=True, metric_dimensions=["region"], metric_catalog_version=catalog["version"]
    )
    data = await call(server, "metric_query", values)
    payload = client.post(
        "/api/v1/query", json=body(execute=True, metric_dimensions=["region"])
    ).json()
    assert data["status"] == "completed" and data["executed"] is True
    assert data["sql"] == payload["primary_sql"]
    assert data["rows"] == payload["execution"]["rows"]
    assert data["validation"]["passed"] is True
    assert data["plan_output"] == payload["plan_output"]
    assert data["llm_calls"] == 0 and model.calls == []


async def test_default_is_validate_without_execution(metric_env):
    _, model, *_ = metric_env
    server = create_nl2sql_server(trust_local_metric_tools=True)
    data = await call(server, "metric_query", args())
    assert data["status"] == "completed" and data["executed"] is False
    assert data["rows"] == [] and data["row_count"] == 0
    assert data["validation"]["passed"] is True and data["sql"]
    assert data["llm_calls"] == 0 and model.calls == []


@pytest.mark.parametrize("overrides", [
    {"domain": "finance"}, {"db_id": "missing"}, {"metric_catalog_version": "old"},
    {"metrics": ["unknown"]}, {"metric_dimensions": ["amount"]},
])
async def test_invalid_scope_and_version_keep_error_contract(metric_env, overrides):
    _, model, *_ = metric_env
    server = create_nl2sql_server(trust_local_metric_tools=True)
    data = await call(server, "metric_query", args(**overrides))
    assert data["status"] == "failed" and data["error_code"] == "INVALID_REQUEST"
    assert data["http_status"] == 400 and data["rows"] == [] and model.calls == []


async def test_default_local_process_is_not_implicitly_trusted(metric_env):
    _, model, *_ = metric_env
    server = create_nl2sql_server()
    for name, arguments in (
        ("list_metrics", {"domain": "ecommerce", "db_id": "metric-db"}),
        ("metric_query", args(execute=True)),
    ):
        data = await call(server, name, arguments)
        assert data["status"] == "failed" and data["http_status"] == 401
    assert model.calls == []


async def test_projection_caps_at_100_and_marks_truncated(metric_env):
    _, model, *_ = metric_env
    connection = ConnectionFactory.get("metric-db")
    connection.executemany(
        "INSERT INTO orders(user_id,product_id,amount,region) VALUES(1,1,1,?)",
        [(f"region-{n}",) for n in range(150)],
    )
    connection.commit()
    data = await call(create_nl2sql_server(trust_local_metric_tools=True), "metric_query", args(
        execute=True, metric_dimensions=["region"],
    ))
    assert data["status"] == "completed"
    assert data["row_count"] == len(data["rows"]) == 100 and data["truncated"] is True
    assert "execution" not in data["candidates"][0]
    assert model.calls == []


async def test_http_operator_token_can_discover_and_execute(metric_env):
    _, model, *_ = metric_env
    token = create_access_token(1, "operator")
    server = create_nl2sql_server()
    catalog = await http_call(
        server, "list_metrics", {"domain": "ecommerce", "db_id": "metric-db"}, token
    )
    assert catalog["status"] == "completed"
    data = await http_call(server, "metric_query", args(execute=True), token)
    assert data["status"] == "completed" and data["rows"]
    assert token not in str(data) and model.calls == []


@pytest.mark.parametrize("kind, status", [("missing", 401), ("outsider", 403), ("expired", 401)])
@pytest.mark.parametrize("tool", ["list_metrics", "metric_query"])
async def test_http_auth_cannot_be_bypassed_by_local_trust(metric_env, kind, status, tool):
    _, model, *_ = metric_env
    token = None
    if kind == "outsider":
        token = create_access_token(99, "outsider")
    elif kind == "expired":
        now = datetime.now(UTC)
        token = jwt.encode(
            {"sub": "1", "username": "operator", "iat": now - timedelta(hours=2),
             "exp": now - timedelta(hours=1)},
            settings.jwt_secret, algorithm=settings.jwt_algorithm,
        )
    server = create_nl2sql_server(trust_local_metric_tools=True)
    arguments = args(execute=True) if tool == "metric_query" else {
        "domain": "ecommerce", "db_id": "metric-db",
    }
    data = await http_call(server, tool, arguments, token)
    assert data["status"] == "failed" and data["http_status"] == status
    assert data["rows"] == [] and model.calls == []
    if token:
        assert token not in str(data)


async def test_timeout_capacity_cancel_and_recovery(metric_env, monkeypatch):
    from app.db.runtime import gate, lease

    _, model, *_ = metric_env
    monkeypatch.setattr(settings, "max_concurrent_queries", 1)
    monkeypatch.setattr(settings, "query_timeout_seconds", 0.02)
    connection = ConnectionFactory.get("metric-db")
    server = create_nl2sql_server(trust_local_metric_tools=True)
    async with lease(connection):
        data = await call(server, "metric_query", args(execute=True))
    assert data["error_code"] == "QUERY_TIMEOUT" and data["http_status"] == 504
    monkeypatch.setattr(settings, "query_timeout_seconds", 5)
    async with lease(connection):
        task = asyncio.create_task(call(server, "metric_query", args(execute=True)))
        try:
            async with asyncio.timeout(2):
                while not gate("query-admission", 1).locked():
                    await asyncio.sleep(0.001)
            data = await call(server, "metric_query", args(execute=True))
            assert data["error_code"] == "QUERY_CAPACITY" and data["http_status"] == 503
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    data = await call(server, "metric_query", args(execute=True))
    assert data["status"] == "completed" and data["executed"] is True
    assert model.calls == []


async def test_discovered_version_is_not_silently_upgraded(metric_env):
    _, model, path, _ = metric_env
    server = create_nl2sql_server(trust_local_metric_tools=True)
    catalog = await call(server, "list_metrics", {"domain": "ecommerce", "db_id": "metric-db"})
    updated = path.read_text(encoding="utf-8").replace(catalog["version"], "new-version")
    path.write_text(updated, encoding="utf-8")
    data = await call(server, "metric_query", args(metric_catalog_version=catalog["version"]))
    assert data["status"] == "failed" and data["http_status"] == 400
    assert model.calls == []


async def test_token_and_host_trust_are_not_tool_parameters(metric_env):
    _, model, *_ = metric_env
    server = create_nl2sql_server()
    tools = {tool.name: tool for tool in await server.list_tools()}
    for name in ("list_metrics", "metric_query"):
        params = tools[name].parameters["properties"]
        assert not {"token", "access_token", "trust_local_metric_tools"} & set(params)
    from fastmcp.exceptions import ValidationError

    with pytest.raises(ValidationError):
        await server.call_tool("metric_query", {**args(), "trust_local_metric_tools": True})
    assert model.calls == []
