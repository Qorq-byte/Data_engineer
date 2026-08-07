"""Tests for /api/v1/settings/llm — save settings → router actually switches."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config.settings import settings
from app.llm.factory import reset_default_router


@pytest.fixture
def real_mode(monkeypatch):
    """Clear all mock forcing + provider env keys for controlled tests."""
    monkeypatch.delenv("LLM_MOCK_MODE", raising=False)
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    for env in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return monkeypatch


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_state():
    """Isolate each test: reset the settings store and the router singleton."""
    from app.api.settings import _DEFAULT_SETTINGS, _settings_store

    _settings_store.clear()
    _settings_store.update(copy.deepcopy(_DEFAULT_SETTINGS))
    reset_default_router()
    yield
    reset_default_router()


class TestLLMSettingsSwitch:
    def test_put_default_model_switches_runtime_router(self, client, real_mode):
        """The core regression this feature fixes: saving the settings must
        change the router used by the query pipeline."""
        before = client.get("/api/v1/settings/llm").json()["default_model"]

        resp = client.put(
            "/api/v1/settings/llm",
            json={
                "default_model": "gpt-4.1",
                "providers": {
                    "openai": {"api_key": "sk-openai-test", "enabled": True},
                },
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["active_model"] == "gpt-4.1"
        assert data["mock_mode"] is False

        after = client.get("/api/v1/settings/llm").json()["default_model"]
        assert before != "gpt-4.1"
        assert after == "gpt-4.1"

        from app.llm.factory import get_default_router

        router = get_default_router()
        assert router.config.default_model == "gpt-4.1"
        assert router.config.providers["openai"].api_key == "sk-openai-test"

    def test_api_key_never_returned_in_get(self, client):
        client.put(
            "/api/v1/settings/llm",
            json={
                "providers": {
                    "deepseek": {"api_key": "sk-secret-value", "enabled": True},
                }
            },
        )
        providers = client.get("/api/v1/settings/llm").json()["providers"]
        deepseek = providers["deepseek"]
        assert deepseek["api_key_configured"] is True
        assert "api_key" not in deepseek
        assert "api_key_enc" not in deepseek

    def test_empty_api_key_keeps_existing(self, client, real_mode):
        client.put(
            "/api/v1/settings/llm",
            json={"providers": {"openai": {"api_key": "sk-first-key"}}},
        )
        # Empty key → keep old one
        client.put(
            "/api/v1/settings/llm",
            json={"providers": {"openai": {"api_key": ""}}},
        )
        from app.llm.factory import get_default_router

        assert get_default_router().config.providers["openai"].api_key == "sk-first-key"

    def test_custom_provider_with_base_url_roundtrip(self, client):
        resp = client.put(
            "/api/v1/settings/llm",
            json={
                "providers": {
                    "myproxy": {
                        "enabled": True,
                        "models": ["local-model"],
                        "api_key": "sk-proxy-key",
                        "base_url": "https://proxy.example.com/v1",
                    }
                },
                "default_model": "local-model",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["active_model"] == "local-model"

        providers = client.get("/api/v1/settings/llm").json()["providers"]
        assert "myproxy" in providers
        assert providers["myproxy"]["api_key_configured"] is True
        assert providers["myproxy"]["base_url"] == "https://proxy.example.com/v1"
        assert "api_key_enc" not in providers["myproxy"]

        from app.llm.factory import get_default_router

        proxy = get_default_router().config.providers["myproxy"]
        assert proxy.api_key == "sk-proxy-key"
        assert proxy.base_url == "https://proxy.example.com/v1"

    def test_invalid_routing_strategy_rejected(self, client):
        resp = client.put(
            "/api/v1/settings/llm",
            json={"routing_strategy": "not-a-strategy"},
        )
        assert resp.status_code == 400


class TestQueryModelOverride:
    def test_unknown_model_rejected_with_422(self, client):
        resp = client.post(
            "/api/v1/query",
            json={"nl_text": "查询所有订单", "model": "not-a-real-model"},
        )
        assert resp.status_code == 422
        assert "not-a-real-model" in resp.json()["detail"]

    def test_known_model_accepted(self, client, real_mode):
        client.put(
            "/api/v1/settings/llm",
            json={
                "providers": {"openai": {"api_key": "sk-openai-test"}},
                "default_model": "gpt-4.1",
            },
        )
        resp = client.post(
            "/api/v1/query",
            json={"nl_text": "查询所有订单", "model": "gpt-4.1", "execute": False},
        )
        # 200: model accepted by validation; generation may be mock/empty but
        # the request must not be rejected for the model choice.
        assert resp.status_code == 200
