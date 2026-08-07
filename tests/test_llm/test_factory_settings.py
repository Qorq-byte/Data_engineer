"""Tests for app.llm.factory — settings-driven router construction.

The pytest suite runs with ``LLM_MOCK_MODE=1`` (conftest) — tests that need
real-mode construction clear both the env var and the settings singleton via
the ``real_mode`` fixture.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config.settings import settings
from app.llm.factory import (
    apply_llm_settings,
    build_router_from_settings,
    get_default_router,
    reset_default_router,
)
from app.llm.router import ProviderConfigurationError
from app.security.crypto import encrypt_secret


@pytest.fixture
def real_mode(monkeypatch):
    """Clear all mock forcing + provider env keys for controlled tests."""
    monkeypatch.delenv("LLM_MOCK_MODE", raising=False)
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    for env in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return monkeypatch


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_default_router()
    yield
    reset_default_router()


def _llm_settings(**overrides):
    base = {
        "default_model": "deepseek-v4-flash",
        "fallback_model": "",
        "routing_strategy": "complexity_aware",
        "temperature": 0.3,
        "max_tokens": 4096,
        "providers": {
            "deepseek": {
                "enabled": True,
                "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
                "api_key_configured": True,
                "api_key_enc": encrypt_secret("sk-ds-test"),
                "base_url": None,
            },
        },
    }
    base.update(overrides)
    return base


class TestBuildFromSettings:
    def test_real_router_from_encrypted_key(self, real_mode):
        router = build_router_from_settings(_llm_settings())
        assert router.mock_mode is False
        assert router.config.default_model == "deepseek-v4-flash"
        assert set(router.config.providers) == {"deepseek"}
        # Explicit key from settings decrypts into the provider config
        assert router.config.providers["deepseek"].api_key == "sk-ds-test"

    def test_default_model_falls_back_to_first_available(self, real_mode):
        router = build_router_from_settings(
            _llm_settings(default_model="gpt-4.1")  # not in any provider
        )
        assert router.config.default_model == "deepseek-v4-flash"

    def test_no_usable_provider_returns_mock(self, real_mode):
        settings_dict = _llm_settings()
        settings_dict["providers"]["deepseek"]["api_key_enc"] = ""  # no key
        router = build_router_from_settings(settings_dict)
        assert router.mock_mode is True
        assert router.config.providers == {}

    def test_disabled_provider_excluded(self, real_mode):
        settings_dict = _llm_settings()
        settings_dict["providers"]["deepseek"]["enabled"] = False
        router = build_router_from_settings(settings_dict)
        assert router.mock_mode is True  # nothing usable left

    def test_custom_provider_with_base_url(self, real_mode):
        settings_dict = _llm_settings()
        settings_dict["providers"]["myproxy"] = {
            "enabled": True,
            "models": ["my-model-v1"],
            "api_key_configured": True,
            "api_key_enc": encrypt_secret("sk-proxy"),
            "base_url": "https://proxy.example.com/v1",
        }
        settings_dict["default_model"] = "my-model-v1"
        router = build_router_from_settings(settings_dict)
        assert router.config.default_model == "my-model-v1"
        proxy = router.config.providers["myproxy"]
        assert proxy.api_key == "sk-proxy"
        assert proxy.base_url == "https://proxy.example.com/v1"

    def test_fallback_chain_built(self, real_mode):
        router = build_router_from_settings(
            _llm_settings(fallback_model="deepseek-v4-pro"),
        )
        assert router.config.fallback_chain == ["deepseek-v4-pro"]


class TestCustomProviderRouting:
    def test_custom_provider_without_base_url_raises_config_error(self, real_mode):
        """Custom providers route through openai-compatible endpoints and
        must carry a base_url — otherwise the error surfaces immediately
        (non-retryable) instead of attempting a doomed call."""
        settings_dict = _llm_settings()
        settings_dict["providers"]["myproxy"] = {
            "enabled": True,
            "models": ["my-model-v1"],
            "api_key_configured": True,
            "api_key_enc": encrypt_secret("sk-proxy"),
            "base_url": None,
        }
        settings_dict["default_model"] = "my-model-v1"
        router = build_router_from_settings(settings_dict)

        with pytest.raises(ProviderConfigurationError):
            asyncio.run(router.complete([{"role": "user", "content": "hi"}]))


class TestApplySettings:
    def test_apply_replaces_shared_router(self, real_mode):
        from app.llm import factory as factory_mod

        original = get_default_router()
        assert original.config.default_model == "deepseek-v4-flash"

        router = apply_llm_settings(
            _llm_settings(default_model="deepseek-v4-pro")
        )
        assert get_default_router() is router
        assert router.config.default_model == "deepseek-v4-pro"
        assert factory_mod._default_router is router

    def test_apply_without_args_reads_settings_store(self, real_mode):
        from app.api.settings import _settings_store

        _settings_store["llm"] = _llm_settings(default_model="deepseek-v4-pro")
        router = apply_llm_settings()
        assert router.config.default_model == "deepseek-v4-pro"
