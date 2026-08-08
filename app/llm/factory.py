"""Router factory — build a real LiteLLMRouter from agent.yml + environment.

Bridges the three configuration layers:
  1. ``agent.yml``  — which model to use (``provider.default`` / ``fallback``)
  2. ``.env`` / env — API keys (via pydantic-settings ``Settings``)
  3. Runtime        — provider inference by model-name prefix

Key behaviors:
  - Model names are mapped to providers by prefix (``deepseek-*`` → deepseek,
    ``claude-*`` → anthropic, ``gpt-*``/``o1``/``o3``/``o4`` → openai,
    ``gemini-*`` → google).
  - Keys found in ``Settings`` (loaded from ``.env``) are exported to
    ``os.environ`` so LiteLLM's ``os.getenv``-based key lookup works even
    when the key only lives in the ``.env`` file.
  - If no API key is available for the default model's provider — or
    ``LLM_MOCK_MODE`` is truthy — the router degrades to mock mode so the
    app and tests stay runnable offline.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from app.config.settings import settings
from app.llm.router import LiteLLMRouter, ProviderConfig, RouterConfig

# ── Model-prefix → provider inference rules ────────────────────────
# (prefixes, provider_name, api_key_env, base_url_env)
_PROVIDER_RULES: list[tuple[tuple[str, ...], str, str, str | None]] = [
    (("deepseek",), "deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
    (("claude",), "anthropic", "ANTHROPIC_API_KEY", None),
    (("gpt", "o1", "o3", "o4"), "openai", "OPENAI_API_KEY", "OPENAI_BASE_URL"),
    (("gemini",), "google", "GOOGLE_API_KEY", None),
]

# Settings attribute holding each provider's key (pydantic reads .env,
# which does NOT export to os.environ — we bridge that gap here).
_SETTINGS_KEY_ATTR = {
    "DEEPSEEK_API_KEY": "deepseek_api_key",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "OPENAI_API_KEY": "openai_api_key",
    "GOOGLE_API_KEY": "google_api_key",
}

_DEFAULT_BASE_URLS = {"deepseek": settings.deepseek_base_url}

_TRUTHY = {"1", "true", "yes", "on"}


def infer_provider(model: str) -> tuple[str, str, str | None]:
    """Infer (provider_name, api_key_env, base_url_env) from a model name.

    Unknown models fall through to openai (LiteLLM passes unknown models
    via the OpenAI-compatible endpoint).
    """
    lowered = model.lower()
    for prefixes, provider, key_env, url_env in _PROVIDER_RULES:
        if lowered.startswith(prefixes):
            return provider, key_env, url_env
    return "openai", "OPENAI_API_KEY", "OPENAI_BASE_URL"


def _resolve_api_key(key_env: str) -> str:
    """Look up an API key: os.environ first, then Settings (.env)."""
    key = os.getenv(key_env, "")
    if key:
        return key
    attr = _SETTINGS_KEY_ATTR.get(key_env)
    if attr:
        return getattr(settings, attr, None) or ""
    return ""


def _mock_forced() -> bool:
    """Check whether mock mode is explicitly requested."""
    if os.getenv("LLM_MOCK_MODE", "").strip().lower() in _TRUTHY:
        return True
    return bool(settings.llm_mock_mode)


def _read_agent_provider(config_path: str | None = None) -> tuple[str, list[str]]:
    """Read (default_model, fallback_models) from agent.yml.

    Tolerant of a missing/malformed file — falls back to RouterConfig
    defaults so the factory never blocks startup.
    """
    path = Path(config_path or settings.agent_config_path)
    try:
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        provider = raw.get("agent", {}).get("provider", {}) or {}
        default = provider.get("default") or RouterConfig().default_model
        fallback = list(provider.get("fallback") or [])
        return default, fallback
    except OSError:
        return RouterConfig().default_model, []


def build_router(
    default_model: str | None = None,
    fallback: list[str] | None = None,
    *,
    force_mock: bool | None = None,
    config_path: str | None = None,
) -> LiteLLMRouter:
    """Build a LiteLLMRouter wired to the configured providers.

    Args:
        default_model: Override for agent.yml ``provider.default``.
        fallback: Override for agent.yml ``provider.fallback``.
        force_mock: Explicitly force mock mode on/off (None = auto-detect).
        config_path: Override path to agent.yml (mainly for tests).

    Returns:
        A router in real mode when the default model's provider has an API
        key available, otherwise in mock mode.
    """
    yml_default, yml_fallback = _read_agent_provider(config_path)
    model = default_model or yml_default
    fallback_models = yml_fallback if fallback is None else fallback

    providers: dict[str, ProviderConfig] = {}
    usable_fallback: list[str] = []

    for m in [model, *fallback_models]:
        provider_name, key_env, url_env = infer_provider(m)
        api_key = _resolve_api_key(key_env)
        if not api_key:
            continue  # No key — provider unusable, skip
        # Bridge .env-only keys into os.environ for LiteLLM's lookup
        os.environ.setdefault(key_env, api_key)

        base_url = None
        if url_env:
            base_url = os.getenv(url_env) or _DEFAULT_BASE_URLS.get(provider_name)

        entry = providers.setdefault(
            provider_name,
            ProviderConfig(
                provider=provider_name,
                api_key_env=key_env,
                models=[],
                base_url=base_url,
            ),
        )
        if m not in entry.models:
            entry.models.append(m)
        if m != model:
            usable_fallback.append(m)

    default_provider, _, _ = infer_provider(model)
    if force_mock is not None:
        mock = force_mock
    else:
        mock = _mock_forced() or default_provider not in providers

    config = RouterConfig(
        providers=providers,
        default_model=model,
        fallback_chain=usable_fallback,
    )
    return LiteLLMRouter(config, mock_mode=mock)


# ── Lazy singleton (shared by the API layer) ───────────────────────

_default_router: LiteLLMRouter | None = None


def get_default_router() -> LiteLLMRouter:
    """Return the process-wide shared router (built on first use)."""
    global _default_router
    if _default_router is None:
        _default_router = build_router()
    return _default_router


def reset_default_router() -> None:
    """Drop the cached singleton (tests / config reload)."""
    global _default_router
    _default_router = None


# ── Settings-driven router (user-configured providers from the API) ───

_ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "google": "GOOGLE_API_KEY",
    "gemini": "GOOGLE_API_KEY",
}


def _provider_api_key(name: str, cfg: dict[str, Any]) -> str:
    """Resolve a provider's API key from settings, falling back to env."""
    from app.security.crypto import decrypt_secret

    enc = cfg.get("api_key_enc") or ""
    if enc:
        key = decrypt_secret(enc)
        if key:
            return key
    # Fallback: env key for built-in providers (agent.yml / .env setup)
    key_env = _ENV_KEYS.get(name, "")
    env_key = os.getenv(key_env, "") if key_env else ""
    if env_key:
        return env_key
    # Bridge keys that only live in .env (pydantic Settings) into runtime
    attr = _SETTINGS_KEY_ATTR.get(key_env)
    if attr:
        return getattr(settings, attr, None) or ""
    return ""


def build_router_from_settings(llm_settings: dict[str, Any]) -> LiteLLMRouter:
    """Build a router from the persisted ``llm`` settings section.

    Only *enabled* providers with a usable API key are registered.
    ``default_model`` must belong to some registered provider's models;
    otherwise it falls back to the first available model. When nothing is
    usable, a mock router is returned so the app stays runnable offline.

    Args:
        llm_settings: The ``llm`` dict from ``app.api.settings``
            (``providers``, ``default_model``, ``fallback_model``, …).

    Returns:
        A router in real mode when at least one provider is usable,
        otherwise a mock-mode router.
    """
    providers: dict[str, ProviderConfig] = {}
    provider_cfg: dict[str, Any] = llm_settings.get("providers") or {}

    for name, cfg in provider_cfg.items():
        if not cfg.get("enabled", True):
            continue
        api_key = _provider_api_key(name, cfg)
        if not api_key:
            continue  # No usable key — provider stays out of the chain
        models = [m for m in (cfg.get("models") or []) if m]
        providers[name] = ProviderConfig(
            provider=name,
            api_key_env=_ENV_KEYS.get(name, f"{name.upper()}_API_KEY"),
            api_key=api_key,
            base_url=cfg.get("base_url") or None,
            models=models,
        )

    available_models = [m for pc in providers.values() for m in pc.models]
    default_model = llm_settings.get("default_model") or ""
    if default_model not in available_models:
        default_model = available_models[0] if available_models else (
            llm_settings.get("default_model") or "mock"
        )

    fallback_model = llm_settings.get("fallback_model") or ""
    fallback_chain: list[str] = []
    if fallback_model and fallback_model in available_models and fallback_model != default_model:
        fallback_chain = [fallback_model]

    config = RouterConfig(
        providers=providers,
        default_model=default_model,
        fallback_chain=fallback_chain,
    )
    mock = _mock_forced() or not providers
    return LiteLLMRouter(config, mock_mode=mock)


def apply_llm_settings(llm_settings: dict[str, Any] | None = None) -> LiteLLMRouter:
    """Rebuild and replace the shared router from persisted LLM settings.

    Called after the user saves LLM settings (immediate effect) and at
    startup (persisted settings survive restarts).

    Args:
        llm_settings: Optional ``llm`` settings dict. When omitted, the
            current in-memory settings store is read.

    Returns:
        The new shared router instance.
    """
    global _default_router
    if llm_settings is None:
        try:
            from app.api.settings import _settings_store

            llm_settings = _settings_store.get("llm") or {}
        except Exception:  # noqa: BLE001
            llm_settings = {}
    _default_router = build_router_from_settings(llm_settings)
    return _default_router
