"""Tests for the router factory (app/llm/factory.py).

All tests are hermetic: provider keys and mock flags are controlled via
monkeypatch — no real LLM endpoint is ever contacted.
"""

import pytest

from app.config.settings import settings
from app.llm.factory import (
    build_router,
    get_default_router,
    infer_provider,
    reset_default_router,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_default_router()
    yield
    reset_default_router()


@pytest.fixture
def real_mode_env(monkeypatch):
    """Clear all mock forcing + provider keys for controlled tests."""
    monkeypatch.delenv("LLM_MOCK_MODE", raising=False)
    monkeypatch.setattr(settings, "llm_mock_mode", False)
    for env in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(settings, "deepseek_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "google_api_key", None)
    return monkeypatch


# ── Provider inference ─────────────────────────────────────────────


class TestInferProvider:
    def test_deepseek(self):
        assert infer_provider("deepseek-v4-flash")[0] == "deepseek"

    def test_claude(self):
        assert infer_provider("claude-sonnet-5")[0] == "anthropic"

    def test_gpt(self):
        assert infer_provider("gpt-4.1")[0] == "openai"

    def test_o_series(self):
        assert infer_provider("o4-mini")[0] == "openai"

    def test_gemini(self):
        assert infer_provider("gemini-2.0-flash")[0] == "google"

    def test_unknown_defaults_to_openai(self):
        assert infer_provider("qwen2.5-7b")[0] == "openai"

    def test_case_insensitive(self):
        assert infer_provider("DeepSeek-V4-Flash")[0] == "deepseek"


# ── build_router ───────────────────────────────────────────────────


class TestBuildRouter:
    def test_real_mode_with_env_key(self, real_mode_env):
        real_mode_env.setenv("DEEPSEEK_API_KEY", "sk-test")
        router = build_router("deepseek-v4-flash", fallback=[])
        assert router.mock_mode is False
        assert router.config.default_model == "deepseek-v4-flash"
        pc = router.config.providers["deepseek"]
        assert pc.api_key_env == "DEEPSEEK_API_KEY"
        assert "deepseek-v4-flash" in pc.models
        assert pc.base_url == settings.deepseek_base_url

    def test_settings_key_bridged_to_environ(self, real_mode_env):
        """A key only present in .env (Settings) must reach os.environ."""
        import os

        real_mode_env.setattr(settings, "deepseek_api_key", "sk-from-dotenv")
        router = build_router("deepseek-v4-flash", fallback=[])
        assert router.mock_mode is False
        assert os.environ["DEEPSEEK_API_KEY"] == "sk-from-dotenv"

    def test_no_key_degrades_to_mock(self, real_mode_env):
        router = build_router("deepseek-v4-flash", fallback=[])
        assert router.mock_mode is True

    def test_llm_mock_mode_env_forces_mock(self, real_mode_env):
        real_mode_env.setenv("DEEPSEEK_API_KEY", "sk-test")
        real_mode_env.setenv("LLM_MOCK_MODE", "1")
        router = build_router("deepseek-v4-flash", fallback=[])
        assert router.mock_mode is True

    def test_force_mock_true(self, real_mode_env):
        real_mode_env.setenv("DEEPSEEK_API_KEY", "sk-test")
        router = build_router("deepseek-v4-flash", fallback=[], force_mock=True)
        assert router.mock_mode is True

    def test_force_mock_false_overrides_env_flag(self, real_mode_env):
        real_mode_env.setenv("DEEPSEEK_API_KEY", "sk-test")
        real_mode_env.setenv("LLM_MOCK_MODE", "1")
        router = build_router("deepseek-v4-flash", fallback=[], force_mock=False)
        assert router.mock_mode is False

    def test_fallback_without_key_skipped(self, real_mode_env):
        real_mode_env.setenv("DEEPSEEK_API_KEY", "sk-test")
        router = build_router("deepseek-v4-flash", fallback=["gpt-4.1"])
        assert router.config.fallback_chain == []
        assert "openai" not in router.config.providers

    def test_fallback_with_key_registered(self, real_mode_env):
        real_mode_env.setenv("DEEPSEEK_API_KEY", "sk-test")
        real_mode_env.setenv("OPENAI_API_KEY", "sk-oa")
        router = build_router("deepseek-v4-flash", fallback=["gpt-4.1"])
        assert router.config.fallback_chain == ["gpt-4.1"]
        assert "gpt-4.1" in router.config.providers["openai"].models

    def test_base_url_env_override(self, real_mode_env):
        real_mode_env.setenv("DEEPSEEK_API_KEY", "sk-test")
        real_mode_env.setenv("DEEPSEEK_BASE_URL", "http://localhost:8123/v1")
        router = build_router("deepseek-v4-flash", fallback=[])
        assert router.config.providers["deepseek"].base_url == "http://localhost:8123/v1"

    def test_reads_default_from_agent_yml(self, real_mode_env, tmp_path):
        yml = tmp_path / "agent.yml"
        yml.write_text(
            "agent:\n  provider:\n    default: deepseek-v4-flash\n    fallback: []\n",
            encoding="utf-8",
        )
        real_mode_env.setenv("DEEPSEEK_API_KEY", "sk-test")
        router = build_router(config_path=str(yml))
        assert router.config.default_model == "deepseek-v4-flash"
        assert router.mock_mode is False

    def test_missing_agent_yml_no_crash(self, real_mode_env, tmp_path):
        router = build_router(config_path=str(tmp_path / "nope.yml"))
        assert router.mock_mode is True  # no keys → mock, but never raises

    def test_project_agent_yml_selects_deepseek(self, real_mode_env):
        """The repo's real agent.yml points at deepseek-v4-flash."""
        real_mode_env.setenv("DEEPSEEK_API_KEY", "sk-test")
        router = build_router()
        assert router.config.default_model == "deepseek-v4-flash"
        assert router.mock_mode is False


# ── Singleton ──────────────────────────────────────────────────────


class TestDefaultRouterSingleton:
    def test_same_instance(self):
        assert get_default_router() is get_default_router()

    def test_reset_creates_new(self):
        first = get_default_router()
        reset_default_router()
        assert get_default_router() is not first

    def test_mock_under_test_env(self):
        # conftest sets LLM_MOCK_MODE=1 — the shared router must be mock
        assert get_default_router().mock_mode is True
