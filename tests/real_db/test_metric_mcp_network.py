"""External SDK clients use real loopback MCP HTTP and sealed MySQL workers."""

import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from app.auth.security import create_access_token
from tests.fixtures.shared_query_worker import SECRET
from tests.real_db.test_mysql_coordination import coordination as coordination
from tests.real_db.test_shared_query_runtime import shared_servers as shared_servers

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
    pytest.mark.parametrize("shared_servers", [
        {"immutable": True, "metrics": True, "metric_mcp": True},
    ], indirect=True),
]
SCOPE = {"domain": "ecommerce", "db_id": "fixture"}
QUERY = {**SCOPE, "metrics": ["order_count", "id_sum"],
         "metric_catalog_version": "shared-fixture-v1", "execute": True}


def network_client(url, token):
    return Client(StreamableHttpTransport(
        url + "/metric-mcp/", headers={"Authorization": f"Bearer {token}"} if token else {},
    ), timeout=15)


async def tool(client, name, args):
    return (await client.call_tool(name, args)).structured_content


async def test_metric_mcp_network_auth_results_shared_session_and_restart(shared_servers):
    http, servers = shared_servers
    expired = jwt.encode({
        "sub": "1", "username": "fixture", "exp": datetime.now(UTC) - timedelta(minutes=1),
    }, SECRET, algorithm="HS256")
    operator = create_access_token(1, "fixture")
    rejected = [(None, 401), ("malformed-token", 401), (expired, 401),
                (create_access_token(99999, "outsider"), 403)]
    catalogs = []
    for url, directory, _ in servers:
        for token, status in rejected:
            async with network_client(url, token) as client:
                for name, args in (("list_metrics", SCOPE), ("metric_query", QUERY)):
                    result = await tool(client, name, args)
                    assert result["status"] == "failed" and result["http_status"] == status
                    assert result["rows"] == [] and "token" not in result["error"]
        async with network_client(url, operator) as client:
            catalog = await tool(client, "list_metrics", SCOPE)
            rest = await http.get(url + "/api/v1/metrics?domain=ecommerce&database=fixture")
            assert catalog == {"status": "completed", **rest.json()}
            catalogs.append(catalog)
            result = await tool(client, "metric_query", QUERY)
            assert result["status"] == "completed" and result["rows"] == [[5, "150"]]
            assert result["llm_calls"] == 0 and result["validation"]["passed"] is True
            assert result["plan_output"]["node_names"] == [
                "metric_resolve", "validate_sql", "execute_sql",
            ]
            assert result["plan_output"]["metric_catalog"]["sha256"] == catalog["sha256"]
            other = servers[1][0] if url == servers[0][0] else servers[0][0]
            session = await http.get(other + f"/api/v1/sessions/{result['session_id']}")
            assert session.status_code == 200 and len(session.json()["session"]["turns"]) == 1
        assert not (directory / "model-calls.txt").exists()
    assert catalogs[0] == catalogs[1]
    servers[0][2].kill()
    await servers[0][2].wait()
    replacement, directory, _ = await servers.launch("replacement")
    async with network_client(replacement, operator) as client:
        assert await tool(client, "list_metrics", SCOPE) == catalogs[0]
        assert (await tool(client, "metric_query", QUERY))["rows"] == [[5, "150"]]
    assert not (directory / "model-calls.txt").exists()


@pytest.mark.parametrize("drift", ["catalog", "control_plane"])
async def test_metric_mcp_network_rejects_shared_configuration_drift(
    shared_servers, coordination, drift,
):
    http, servers = shared_servers
    catalog = servers[0][1].parent / "metrics.yml"
    if drift == "catalog":
        state = catalog.stat()
        catalog.write_text(catalog.read_text(encoding="utf-8").replace(
            "aggregation: sum", "aggregation: max"
        ), encoding="utf-8")
        assert catalog.stat().st_size == state.st_size
        os.utime(catalog, ns=(state.st_atime_ns, state.st_mtime_ns))
    else:
        _, pool, _, _ = coordination
        async with pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute("UPDATE user_settings SET config='{}' WHERE section='llm'")
    for url, directory, _ in servers:
        async with network_client(url, create_access_token(1, "fixture")) as client:
            for name, args in (("list_metrics", SCOPE), ("metric_query", QUERY)):
                result = await tool(client, name, args)
                assert result["status"] == "failed"
                assert result["error_code"] == "SHARED_STATE_UNAVAILABLE"
                assert result["http_status"] == 503 and str(catalog) not in str(result)
        assert (await http.get(url + "/health/ready")).status_code == 503
        assert not (directory / "model-calls.txt").exists()
