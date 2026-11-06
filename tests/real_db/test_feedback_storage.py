"""Feedback entry points against an owned MySQL schema, with no model requests."""

import importlib
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.agents.feedback import FeedbackAgent
from app.api import feedback
from app.storage.mysql_store import MySQLStore
from tests.real_db.support import admin_connection, admin_sql

pytestmark = [pytest.mark.real_database,
              pytest.mark.parametrize("service", ["mysql"], indirect=True)]


@pytest_asyncio.fixture
async def feedback_store(service, monkeypatch):
    # Separate schema: application tables must not pollute the query fixtures.
    database = f"de_feedback56_{uuid.uuid4().hex}"
    config = service.config(admin=True)
    config["db"] = database
    del config["database"]
    monkeypatch.setattr(MySQLStore, "_instance", None)
    monkeypatch.setattr(feedback, "_feedback_store", [])
    collector = importlib.import_module("app.learning.feedback_collector").feedback_collector

    def unavailable(**kwargs):
        raise RuntimeError("synthetic collector outage")

    monkeypatch.setattr(collector, "collect", unavailable)
    async with admin_connection(service) as admin:
        await admin_sql(service, admin, f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
        try:
            store = await MySQLStore.create(config)
            await store.init_tables()
            yield store, config
        finally:
            if MySQLStore._instance is not None:
                await MySQLStore._instance.close()
            await admin_sql(service, admin, f"DROP DATABASE `{database}`")


async def submit(path):
    if path == "agent":
        result = await FeedbackAgent().execute(
            {"turn_id": "q56", "rating": 4, "comment": "缺少状态过滤🙂",
             "original_sql": "SELECT 1", "final_sql": "SELECT 2"},
            {"session_id": "s56", "nl_input": "订单总额", "domain_id": "sales"},
        )
        return result.data
    app = FastAPI()
    app.include_router(feedback.router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://fixture") as client:
        response = await client.post("/api/v1/feedback", json={
            "query_id": "q56", "session_id": "s56", "nl_input": "订单总额",
            "sql_generated": "SELECT 1", "sql_final": "SELECT 2",
            "rating": 4, "feedback_text": "缺少状态过滤🙂", "domain": "sales",
        })
        assert response.status_code == 200
        return response.json()


@pytest.mark.parametrize("path", ["rest", "agent"])
async def test_entry_point_persists_all_fields_and_reopens(feedback_store, path):
    store, config = feedback_store
    result = await submit(path)
    assert result["persisted"] is True
    assert result["storage"] == "mysql"
    if path == "rest":
        assert result["learning"] is False
        assert "已写入 MySQL" in result["message"]
    # Reopen the actual pool; reads cannot come from a process-local fallback.
    await store.close()
    reopened = await MySQLStore.create(config)
    rows = await reopened.get_recent_feedback()
    assert len(rows) == 1
    row = rows[0]
    for name, expected in {
        "query_id": "q56", "session_id": "s56", "nl_input": "订单总额",
        "sql_generated": "SELECT 1", "sql_final": "SELECT 2", "rating": 4,
        "feedback_text": "缺少状态过滤🙂", "domain": "sales",
    }.items():
        assert row[name] == expected
    assert row["feedback_id"]
    assert (await reopened.get_feedback_stats())["total"] == 1


@pytest.mark.parametrize("path", ["rest", "agent"])
async def test_sql_failure_reports_memory_fallback(feedback_store, path):
    store, _ = feedback_store
    # A real MySQL missing-table failure, rather than a mocked save method.
    await store._execute("DROP TABLE feedback")
    result = await submit(path)
    assert result["storage"] == "process_memory"
    assert result["persisted"] is False
    if path == "rest":
        assert result["learning"] is False
        assert len(feedback._feedback_store) == 1
