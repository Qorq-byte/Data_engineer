"""Tenant isolation checks for runtime session and plan-history keys."""

from __future__ import annotations

from hashlib import sha256

import pytest
from fastapi import HTTPException

from app.api import query
from app.auth.tenant import reset_current_tenant_id, set_current_tenant_id
from app.harness.session_keys import shared_session_digest
from app.workflow.history_store import PlanHistoryStore


def test_plan_history_isolated_by_tenant() -> None:
    store = PlanHistoryStore(":memory:")
    first = set_current_tenant_id(1)
    try:
        store.record("same question", "ez_query")
    finally:
        reset_current_tenant_id(first)

    second = set_current_tenant_id(2)
    try:
        assert store.count() == 0
        store.record("same question", "request_query")
        assert [item.plan_id for item in store.find_similar("same question")] == [
            "request_query"
        ]
    finally:
        reset_current_tenant_id(second)

    third = set_current_tenant_id(1)
    try:
        assert [item.plan_id for item in store.find_similar("same question")] == ["ez_query"]
    finally:
        reset_current_tenant_id(third)
        store.close()


def test_shared_session_digest_keeps_original_default_hash() -> None:
    default = shared_session_digest("same")
    assert default == sha256(b"same").hexdigest()
    legacy_other = shared_session_digest("2:same")
    tenant = set_current_tenant_id(2)
    try:
        assert shared_session_digest("same") not in {default, legacy_other}
    finally:
        reset_current_tenant_id(tenant)


async def test_local_query_session_versions_are_scoped(monkeypatch) -> None:
    monkeypatch.setattr(query, "_SESSION_STORE", {})
    first, _ = await query._load_query_session("same")
    first["turns"].append({"nl_input": "first", "sql": "SELECT 1"})
    tenant = set_current_tenant_id(2)
    try:
        with pytest.raises(HTTPException) as denied:
            await query.get_session("same")
        assert denied.value.status_code == 404
        second, _ = await query._load_query_session("same")
        second["turns"].append({"nl_input": "second", "sql": "SELECT 2"})
        branch = await query.branch_from_version("same", 1, {"edited_sql": "SELECT 3"})
        rollback = await query.rollback_to_version("same", 1)
        assert branch["new_version"]["sql_text"] == "SELECT 3"
        assert rollback["new_version"]["sql_text"] == "SELECT 2"
        assert (await query.get_session("same"))["session"]["session_id"] == "same"
    finally:
        reset_current_tenant_id(tenant)
    result = await query.get_session_versions("same")
    assert result["total"] == 1
    assert result["versions"][0]["sql_text"] == "SELECT 1"
