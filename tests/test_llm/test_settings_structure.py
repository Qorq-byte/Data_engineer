"""Malformed saved settings fail before credentials are consumed or routers published."""

import copy

import pytest

from app.config.settings import settings
from app.llm import factory
from app.llm.router import LiteLLMRouter, RouterConfig
from app.security.crypto import encrypt_secret


@pytest.fixture(autouse=True)
def isolated_router(monkeypatch):
    monkeypatch.setattr(factory, "_default_router", None)
    monkeypatch.setattr(factory, "_mock_forced", lambda: False)
    for provider in ("openai", "anthropic", "deepseek", "google"):
        monkeypatch.delenv(f"{provider.upper()}_API_KEY", raising=False)
        monkeypatch.setattr(settings, f"{provider}_api_key", None)

    async def forbidden(*args, **kwargs):
        pytest.fail("Settings validation must not call a model")

    monkeypatch.setattr(LiteLLMRouter, "_call_provider", forbidden)


def valid_config():
    return {
        "default_model": "fixture-model", "fallback_model": "",
        "providers": {"fixture": {
            "models": ["fixture-model"], "api_key_enc": encrypt_secret("synthetic-key"),
        }},
    }


@pytest.mark.parametrize(("field", "value"), [
    ("models", "private-model-value"), ("models", {"private": "value"}),
    ("models", ["model", None]), ("models", [False]), ("models", ("model",)),
    ("enabled", "false"), ("enabled", None), ("enabled", 0),
    ("api_key_configured", "false"), ("api_key_configured", None),
    ("api_key_enc", {"private": "value"}), ("base_url", 123),
    ("rate_limit_rpm", "60"), ("rate_limit_rpm", True),
    ("rate_limit_rpm", -1), ("rate_limit_rpm", None), ("rate_limit_rpm", 1.5),
])
def test_bad_provider_structure_rejected_before_key_lookup(monkeypatch, field, value):
    config = valid_config()
    config["providers"]["fixture"][field] = value

    def unexpected_key_lookup(*args):
        pytest.fail("The whole saved record must be validated before reading a key")

    monkeypatch.setattr(factory, "_provider_api_key", unexpected_key_lookup)
    with pytest.raises(ValueError) as caught:
        factory.build_router_from_settings(config)
    assert "private" not in str(caught.value)
    assert "synthetic-key" not in str(caught.value)


@pytest.mark.parametrize("skipped", ["disabled", "no_key"])
def test_invalid_skipped_provider_cannot_hide_behind_usable_provider(monkeypatch, skipped):
    config = valid_config()
    config["providers"]["broken"] = {
        "enabled": skipped != "disabled", "api_key_enc": "", "models": "bad-models",
    }

    def unexpected_key_lookup(*args):
        pytest.fail("All provider records must be checked before resolving any credentials")

    monkeypatch.setattr(factory, "_provider_api_key", unexpected_key_lookup)
    with pytest.raises(ValueError):
        factory.build_router_from_settings(config)


@pytest.mark.parametrize("config", [
    None, [], {"providers": None}, {"providers": []},
    {"providers": {"": {}}}, {"providers": {"  ": {}}},
    {"providers": {42: {}}}, {"providers": {"fixture": []}},
    {"default_model": False}, {"fallback_model": ["model"]},
])
def test_invalid_container_and_model_types_rejected(config):
    with pytest.raises(ValueError):
        factory.build_router_from_settings(config)


def test_error_does_not_expose_provider_name_or_bad_value():
    config = {"providers": {"private-provider-name": {"enabled": "private-field-value"}}}
    with pytest.raises(ValueError) as caught:
        factory.build_router_from_settings(config)
    assert "private-provider-name" not in str(caught.value)
    assert "private-field-value" not in str(caught.value)


@pytest.mark.parametrize("implicit", [False, True])
def test_failed_apply_keeps_published_router_and_input(monkeypatch, implicit):
    from app.api import settings as api_settings

    original = LiteLLMRouter(RouterConfig(default_model="original"), mock_mode=False)
    factory.publish_llm_router(original)
    config = valid_config()
    config["providers"]["fixture"]["enabled"] = "false"
    before = copy.deepcopy(config)
    monkeypatch.setattr(api_settings, "_settings_store", {"llm": config})
    with pytest.raises(ValueError):
        if implicit:
            factory.apply_llm_settings()
        else:
            factory.apply_llm_settings(config)
    assert factory.current_llm_router() is original
    assert original.config.default_model == "original"
    assert config == before


def test_missing_fields_keep_defaults_and_legacy_fields_are_not_interpreted():
    config = {"routing_strategy": {"legacy": "ignored"}, "temperature": "legacy"}
    before = copy.deepcopy(config)
    router = factory.build_router_from_settings(config)
    assert router.config.max_output_tokens == 4096
    assert router.config.providers == {}
    assert router.mock_mode is True
    assert config == before


@pytest.mark.parametrize("models", [None, []])
def test_nullable_text_and_empty_models_remain_compatible(models):
    config = {
        "default_model": None, "fallback_model": None,
        "providers": {"fixture": {
            "models": models, "api_key_enc": None, "base_url": None, "rate_limit_rpm": 0,
        }},
    }
    before = copy.deepcopy(config)
    router = factory.build_router_from_settings(config)
    assert router.mock_mode is True
    assert router.config.default_model == "mock"
    assert router.config.fallback_chain == []
    assert config == before


def test_omitted_flags_zero_rate_and_empty_text_preserve_existing_behavior():
    config = valid_config()
    config["default_model"] = ""
    config["providers"]["fixture"].update({
        "models": ["", "fixture-model", "fallback-model"], "base_url": "", "rate_limit_rpm": 0,
    })
    config["fallback_model"] = "fallback-model"
    router = factory.build_router_from_settings(config)
    provider = router.config.providers["fixture"]
    assert provider.models == ["fixture-model", "fallback-model"]
    assert provider.rate_limit_rpm == 0
    assert provider.base_url is None
    assert router.config.default_model == "fixture-model"
    assert router.config.fallback_chain == ["fallback-model"]
