"""Sealed catalogs in two real serving processes with disposable MySQL state."""

import json
import os

import pytest

from tests.real_db.test_mysql_coordination import coordination as coordination
from tests.real_db.test_shared_query_runtime import shared_servers as shared_servers

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.parametrize("service", ["mysql"], indirect=True),
    pytest.mark.parametrize(
        "shared_servers", [{"immutable": True, "metrics": True}], indirect=True,
    ),
]


def metric_body(**overrides):
    return {
        "nl_text": "approved fixture metrics", "domain": "ecommerce", "database": "fixture",
        "plan_id": "metric_query", "metrics": ["order_count", "id_sum"],
        "metric_catalog_version": "shared-fixture-v1", "num_candidates": 1,
        "execute": True, "session_id": "metric-shared", **overrides,
    }


async def test_shared_metric_catalog_discovery_json_sse_and_restart(shared_servers):
    client, servers = shared_servers
    catalogs = []
    for index, (url, directory, _) in enumerate(servers):
        response = await client.get(url + "/api/v1/metrics?domain=ecommerce&database=fixture")
        assert response.status_code == 200, response.text
        catalogs.append(response.json())
        path = "/api/v1/query" if index == 0 else "/api/v1/query/stream"
        response = await client.post(url + path, json=metric_body())
        assert response.status_code == 200, response.text
        result = response.json() if index == 0 else next(
            json.loads(block.split("data: ", 1)[1])
            for block in response.text.split("\n\n") if block.startswith("event: done\n")
        )
        assert result["status"] == "completed", result
        # MySQL SUM(integer) is DECIMAL; the existing JSON contract preserves it as text.
        assert result["execution"]["rows"] == [[5, "150"]]
        assert result["llm_calls"] == 0 and result["validation"]["passed"] is True
        assert result["plan_output"]["node_names"] == [
            "metric_resolve", "validate_sql", "execute_sql",
        ]
        assert result["plan_output"]["metric_catalog"]["sha256"] == catalogs[-1]["sha256"]
        assert not (directory / "model-calls.txt").exists()
    assert catalogs[0] == catalogs[1]
    assert catalogs[0]["version"] == "shared-fixture-v1"
    assert len(catalogs[0]["sha256"]) == 64
    session = await client.get(servers[0][0] + "/api/v1/sessions/metric-shared")
    assert len(session.json()["session"]["turns"]) == 2
    servers[0][2].kill()
    await servers[0][2].wait()
    url, directory, _ = await servers.launch("replacement")
    response = await client.get(url + "/api/v1/metrics?domain=ecommerce&database=fixture")
    assert response.json() == catalogs[0]
    restored = await client.get(url + "/api/v1/sessions/metric-shared")
    assert len(restored.json()["session"]["turns"]) == 2
    assert (await client.get(url + "/health/ready")).status_code == 200
    assert not (directory / "model-calls.txt").exists()


async def test_shared_metric_catalog_content_drift_fences_all_workers(shared_servers):
    client, servers = shared_servers
    catalog = servers[0][1].parent / "metrics.yml"
    state = catalog.stat()
    catalog.write_text(
        catalog.read_text(encoding="utf-8").replace("aggregation: sum", "aggregation: max"),
        encoding="utf-8",
    )
    assert catalog.stat().st_size == state.st_size
    os.utime(catalog, ns=(state.st_atime_ns, state.st_mtime_ns))
    for url, directory, _ in servers:
        assert (await client.get(url + "/health/ready")).status_code == 503
        for response in (
            await client.get(url + "/api/v1/metrics?domain=ecommerce&database=fixture"),
            await client.post(url + "/api/v1/query", json=metric_body()),
        ):
            assert response.status_code == 503, response.text
            assert response.json()["detail"]["code"] == "SHARED_STATE_UNAVAILABLE"
            assert str(catalog) not in response.text
        stream = await client.post(url + "/api/v1/query/stream", json=metric_body())
        assert stream.status_code == 200
        assert 'event: error' in stream.text and '"code": "SHARED_STATE_UNAVAILABLE"' in stream.text
        assert 'event: done' not in stream.text and str(catalog) not in stream.text
        assert not (directory / "model-calls.txt").exists()


async def test_shared_metric_catalog_configuration_mismatch_cannot_serve(shared_servers):
    client, servers = shared_servers
    with pytest.raises(AssertionError, match="Immutable metric catalog is unavailable"):
        await servers.launch("wrong-catalog", overrides={
            "METRIC_CATALOG_PATH": str(servers[0][1].parent / "unsealed.yml"),
        })
    # A worker with a different environment never changes the active pair's definition.
    for url, _, _ in servers:
        assert (await client.get(url + "/health/ready")).status_code == 200


async def test_shared_metric_discovery_observes_control_plane_drift(shared_servers, coordination):
    client, servers = shared_servers
    _, pool, _, _ = coordination
    async with pool.acquire() as connection, connection.cursor() as cursor:
        await cursor.execute("UPDATE user_settings SET config='{}' WHERE section='llm'")
    for url, directory, _ in servers:
        response = await client.get(url + "/api/v1/metrics?domain=ecommerce&database=fixture")
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "SHARED_STATE_UNAVAILABLE"
        assert not (directory / "model-calls.txt").exists()
