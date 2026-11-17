"""Approved rules reach generation prompts, validation and the response."""

from __future__ import annotations

import pytest

from app.api import create_app, query
from app.db.connections import ConnectionFactory, DatabaseType
from app.learning.rule_registry import configure_learned_rules
from app.llm.router import LiteLLMRouter, RouterConfig
from tests.api_client import authenticated_client


class CaptureRouter(LiteLLMRouter):
    def __init__(self, reply: str = "SELECT id FROM orders") -> None:
        super().__init__(RouterConfig(), mock_mode=True)
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        return {"choices": [{"message": {"content": self.reply}}]}

    @property
    def prompt_text(self) -> str:
        return "\n".join(
            message.get("content", "")
            for message in (self.calls[0] if self.calls else [])
        )


@pytest.fixture
async def rules_env(test_sqlite_db, monkeypatch, tmp_path):
    monkeypatch.setattr(ConnectionFactory, "_connections", {})
    monkeypatch.setattr(ConnectionFactory, "_infos", {})
    registry = configure_learned_rules(tmp_path / "learned_rules.db")
    await ConnectionFactory.create(
        DatabaseType.SQLITE, {"path": test_sqlite_db}, db_id="rules"
    )
    router = CaptureRouter()
    monkeypatch.setattr(query, "get_default_router", lambda: router)
    client = authenticated_client(create_app())
    yield client, registry, router
    ConnectionFactory.close("rules")
    configure_learned_rules(None)


def _approve(registry, rule_id="cr_rules", **payload):
    values = {
        "description": "订单查询必须带条件",
        "pattern": "订单",
        "suggested_enforcement": {"require_where": "true"},
        "domain": "",
        "confidence": 0.9,
        "source_count": 4,
    }
    values.update(payload)
    registry.record_review(rule_id, "approved", values)


def _body(**overrides):
    values = {
        "nl_text": "列出订单",
        "database": "rules",
        "execute": False,
        "run_validation": True,
        "num_candidates": 1,
    }
    values.update(overrides)
    return values


async def test_approved_rule_enters_prompt_and_validation(rules_env):
    client, registry, router = rules_env
    _approve(registry)

    payload = client.post("/api/v1/query", json=_body()).json()

    assert payload["applied_rules"] == [
        {
            "id": "cr_rules",
            "description": "订单查询必须带条件",
            "pattern": "订单",
            "source": "learned",
        }
    ]
    assert "订单查询必须带条件" in router.prompt_text
    validation = payload["validation"]
    reported = validation["business_warnings"] + validation["warnings"]
    assert any("RULE_WARNING" in warning for warning in reported)


async def test_non_matching_rule_is_not_applied(rules_env):
    client, registry, router = rules_env
    _approve(registry, pattern="退款")

    payload = client.post("/api/v1/query", json=_body()).json()

    assert payload["applied_rules"] == []
    assert "订单查询必须带条件" not in router.prompt_text


async def test_rejected_rule_is_not_applied(rules_env):
    client, registry, router = rules_env
    _approve(registry)
    registry.record_review("cr_rules", "rejected", {"description": "订单查询必须带条件"})

    payload = client.post("/api/v1/query", json=_body()).json()

    assert payload["applied_rules"] == []
    assert "订单查询必须带条件" not in router.prompt_text


async def test_domain_scoped_rule_only_applies_to_its_domain(rules_env):
    client, registry, _router = rules_env
    _approve(registry, domain="finance")

    payload = client.post("/api/v1/query", json=_body(domain="default")).json()

    assert payload["applied_rules"] == []


async def test_unreadable_store_degrades_with_a_warning(rules_env, tmp_path):
    client, _registry, _router = rules_env
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    configure_learned_rules(blocked / "rules.db")

    payload = client.post("/api/v1/query", json=_body()).json()

    assert payload["status"] in {"completed", "failed"}
    assert payload["applied_rules"] == []
    assert any("学习规则存储不可读" in warning for warning in payload["warnings"])


async def test_review_endpoint_persists_to_the_learned_store(rules_env):
    client, registry, _router = rules_env
    from app.api import learning as learning_api

    learning_api._learning_store["rule_candidates"].append(
        {
            "id": "cr_api",
            "description": "按地区聚合需要 GROUP BY",
            "pattern": "地区",
            "sql_template": "地区",
            "confidence": 0.7,
            "source_count": 2,
            "status": "pending_review",
            "domain_id": "",
            "suggested_enforcement": {"require_where": "true"},
        }
    )

    response = client.post(
        "/api/v1/learning/rule-candidates/review",
        params={"rule_id": "cr_api", "action": "approve"},
    )

    assert response.status_code == 200
    stored = registry.store.get("cr_api")
    assert stored is not None
    assert stored.status == "approved"
    assert stored.pattern == "地区"
    assert stored.enforce == ["require_where=true"]

    # Cleanup so later tests do not see this candidate.
    learning_api._learning_store["rule_candidates"] = [
        item
        for item in learning_api._learning_store["rule_candidates"]
        if item.get("id") != "cr_api"
    ]


async def test_rule_store_is_optional_for_review(rules_env, tmp_path):
    client, _registry, _router = rules_env
    from app.api import learning as learning_api

    learning_api._learning_store["rule_candidates"].append(
        {
            "id": "cr_nostore",
            "description": "x",
            "pattern": "x",
            "sql_template": "x",
            "confidence": 0.1,
            "source_count": 1,
            "status": "pending_review",
            "domain_id": "",
        }
    )
    blocked = tmp_path / "blocked_store"
    blocked.write_text("not a directory", encoding="utf-8")
    configure_learned_rules(blocked / "rules.db")

    response = client.post(
        "/api/v1/learning/rule-candidates/review",
        params={"rule_id": "cr_nostore", "action": "approve"},
    )

    assert response.status_code == 200
    learning_api._learning_store["rule_candidates"] = [
        item
        for item in learning_api._learning_store["rule_candidates"]
        if item.get("id") != "cr_nostore"
    ]
