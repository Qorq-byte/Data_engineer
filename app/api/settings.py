"""Settings API endpoints — LLM provider config, system settings, API keys.

See SPEC §4.8 (Frontend & API) for the full specification.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()
logger = logging.getLogger(__name__)

# ── In-memory settings store (MySQL fallback) ────────

_settings_store: dict[str, Any] = {}


def _get_store():
    """Get the MySQL store, or return None if not available."""
    try:
        from app.storage.mysql_store import get_store
        return get_store()
    except Exception:
        return None


async def _load_settings_from_db() -> None:
    """Load settings from MySQL, falling back to defaults."""
    global _settings_store
    store = _get_store()
    if store is not None:
        try:
            db_settings = await store.get_settings()
            if db_settings:
                _settings_store = db_settings
                return
        except Exception:
            pass
    # Only fill in missing sections from defaults — don't overwrite existing
    for key, value in _DEFAULT_SETTINGS.items():
        if key not in _settings_store:
            _settings_store[key] = value


async def _persist_section(section: str) -> None:
    """Persist a settings section to MySQL."""
    store = _get_store()
    if store is not None:
        try:
            await store.save_setting(section, _settings_store.get(section, {}))
        except Exception:
            pass


async def _load_setting_section(section: str) -> None:
    """Ensure a settings section is loaded from MySQL."""
    store = _get_store()
    if store is not None:
        try:
            db_config = await store.get_setting(section)
            if db_config:
                _settings_store[section] = db_config
                return
        except Exception:
            pass
    if section not in _settings_store:
        await _load_settings_from_db()


# Copy of default settings for fallback
_DEFAULT_SETTINGS: dict[str, Any] = {
    "llm": {
        "default_model": "deepseek-v4-flash",
        "fallback_model": "",
        "routing_strategy": "complexity_aware",
        "temperature": 0.3,
        "max_tokens": 4096,
        "providers": {
            "openai": {
                "enabled": True,
                "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o"],
                "api_key_configured": False,
                "api_key_enc": "",
                "base_url": None,
            },
            "anthropic": {
                "enabled": True,
                "models": ["claude-sonnet-4", "claude-haiku-4.5"],
                "api_key_configured": False,
                "api_key_enc": "",
                "base_url": None,
            },
            "deepseek": {
                "enabled": True,
                "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
                "api_key_configured": False,
                "api_key_enc": "",
                "base_url": None,
            },
            "google": {
                "enabled": True,
                "models": ["gemini-2.0-flash", "gemini-2.5-pro"],
                "api_key_configured": False,
                "api_key_enc": "",
                "base_url": None,
            },
            "qwen": {
                "enabled": False,
                "models": ["qwen-max", "qwen-plus"],
                "api_key_configured": False,
                "api_key_enc": "",
                "base_url": None,
            },
            "groq": {
                "enabled": False,
                "models": ["llama-3.1-70b", "mixtral-8x7b"],
                "api_key_configured": False,
                "api_key_enc": "",
                "base_url": None,
            },
        },
    },
    "harness": {
        "read_only": True,
        "max_llm_calls": 10,
        "max_result_rows": 1000,
        "query_timeout_s": 30,
        "session_ttl_s": 3600,
        "max_retries": 3,
        "max_candidates": 3,
    },
    "database": {
        "dialects_enabled": {
            "sqlite": True,
            "duckdb": True,
            "postgresql": True,
            "mysql": True,
            "clickhouse": True,
            "snowflake": True,
            "starrocks": True,
            "bigquery": False,
            "redshift": False,
            "databricks": False,
            "trino": False,
        },
        "connection_pool_size": 5,
        "connection_timeout_s": 10,
    },
    "rag": {
        "bm25_weight": 0.3,
        "vector_weight": 0.7,
        "top_k": 10,
        "similarity_threshold": 0.6,
        "embedding_model": "text-embedding-3-small",
    },
}


# ── Request/Response models ────────────────────────────────────────────


class LLMProviderUpdate(BaseModel):
    enabled: bool | None = None
    api_key: str | None = Field(default=None, max_length=512)
    base_url: str | None = None
    models: list[str] | None = None


class LLMSettingsUpdate(BaseModel):
    default_model: str | None = None
    fallback_model: str | None = None
    routing_strategy: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    providers: dict[str, LLMProviderUpdate] | None = None


class HarnessSettingsUpdate(BaseModel):
    read_only: bool | None = None
    max_llm_calls: int | None = Field(default=None, ge=1, le=100)
    max_result_rows: int | None = Field(default=None, ge=1, le=100000)
    query_timeout_s: int | None = Field(default=None, ge=1, le=300)
    session_ttl_s: int | None = Field(default=None, ge=60, le=86400)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    max_candidates: int | None = Field(default=None, ge=1, le=5)


class DatabaseSettingsUpdate(BaseModel):
    dialects_enabled: dict[str, bool] | None = None
    connection_pool_size: int | None = Field(default=None, ge=1, le=50)
    connection_timeout_s: int | None = Field(default=None, ge=1, le=60)


class RagSettingsUpdate(BaseModel):
    bm25_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    vector_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1, le=50)
    similarity_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    embedding_model: str | None = None


# ── Endpoints ──────────────────────────────────────────────────────────


@router.get("/settings")
async def get_all_settings() -> dict:
    """Get all system settings."""
    await _load_settings_from_db()
    return {
        "status": "ok",
        "settings": _settings_store,
    }


@router.get("/settings/llm")
async def get_llm_settings() -> dict:
    """Get LLM provider configuration."""
    await _load_setting_section("llm")
    llm = _settings_store["llm"]
    # Mask API key status
    providers_display = {}
    for name, cfg in llm["providers"].items():
        providers_display[name] = {
            "enabled": cfg["enabled"],
            "models": cfg["models"],
            "api_key_configured": cfg["api_key_configured"],
            "base_url": cfg.get("base_url"),
        }
    return {
        "status": "ok",
        "default_model": llm["default_model"],
        "fallback_model": llm["fallback_model"],
        "routing_strategy": llm["routing_strategy"],
        "temperature": llm["temperature"],
        "max_tokens": llm["max_tokens"],
        "providers": providers_display,
    }


@router.put("/settings/llm")
async def update_llm_settings(body: LLMSettingsUpdate) -> dict:
    """Update LLM configuration."""
    await _load_setting_section("llm")
    llm = _settings_store["llm"]
    if body.default_model is not None:
        llm["default_model"] = body.default_model
    if body.fallback_model is not None:
        llm["fallback_model"] = body.fallback_model
    if body.routing_strategy is not None:
        valid_strategies = ["complexity_aware", "cost_first", "latency_first", "round_robin"]
        if body.routing_strategy not in valid_strategies:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid routing strategy. Must be one of: {valid_strategies}",
            )
        llm["routing_strategy"] = body.routing_strategy
    if body.temperature is not None:
        llm["temperature"] = body.temperature
    if body.max_tokens is not None:
        llm["max_tokens"] = body.max_tokens
    if body.providers:
        from app.security.crypto import encrypt_secret

        for name, update in body.providers.items():
            if name in llm["providers"]:
                cfg = llm["providers"][name]
                if update.enabled is not None:
                    cfg["enabled"] = update.enabled
                if update.api_key is not None:
                    # Non-empty key → encrypt and persist; empty → keep old key
                    if update.api_key.strip():
                        cfg["api_key_enc"] = encrypt_secret(update.api_key.strip())
                        cfg["api_key_configured"] = True
                if update.base_url is not None:
                    cfg["base_url"] = update.base_url.strip() or None
                if update.models is not None:
                    cfg["models"] = update.models
            else:
                # New provider — add it
                llm["providers"][name] = {
                    "enabled": update.enabled if update.enabled is not None else True,
                    "models": update.models if update.models is not None else [],
                    "api_key_configured": bool(update.api_key and update.api_key.strip()),
                    "api_key_enc": (
                        encrypt_secret(update.api_key.strip())
                        if update.api_key and update.api_key.strip()
                        else ""
                    ),
                    "base_url": update.base_url.strip() if update.base_url else None,
                }

    await _persist_section("llm")
    # Rebuild the runtime router so the new settings take effect immediately
    try:
        from app.llm.factory import apply_llm_settings

        router = apply_llm_settings(llm)
        return {
            "status": "updated",
            "message": "LLM 配置已更新",
            "active_model": router.config.default_model,
            "mock_mode": router.mock_mode,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"LLM 配置已保存但应用失败,请检查模型与密钥配置: {e}",
        ) from e


@router.get("/settings/harness")
async def get_harness_settings() -> dict:
    """Get Harness constraint settings."""
    await _load_setting_section("harness")
    return {
        "status": "ok",
        **_settings_store["harness"],
    }


@router.put("/settings/harness")
async def update_harness_settings(body: HarnessSettingsUpdate) -> dict:
    """Update Harness constraints."""
    await _load_setting_section("harness")
    harness = _settings_store["harness"]
    updates = body.model_dump(exclude_none=True)
    harness.update(updates)
    await _persist_section("harness")
    return {"status": "updated", "message": "Harness 约束已更新"}


@router.get("/settings/database")
async def get_database_settings() -> dict:
    """Get database connection settings."""
    await _load_setting_section("database")
    return {
        "status": "ok",
        **_settings_store["database"],
    }


@router.put("/settings/database")
async def update_database_settings(body: DatabaseSettingsUpdate) -> dict:
    """Update database connection settings."""
    await _load_setting_section("database")
    db = _settings_store["database"]
    updates = body.model_dump(exclude_none=True)
    db.update(updates)
    await _persist_section("database")
    return {"status": "updated", "message": "数据库设置已更新"}


@router.get("/settings/rag")
async def get_rag_settings() -> dict:
    """Get RAG configuration."""
    await _load_setting_section("rag")
    return {
        "status": "ok",
        **_settings_store["rag"],
    }


@router.put("/settings/rag")
async def update_rag_settings(body: RagSettingsUpdate) -> dict:
    """Update RAG configuration."""
    await _load_setting_section("rag")
    rag = _settings_store["rag"]
    updates = body.model_dump(exclude_none=True)
    rag.update(updates)
    await _persist_section("rag")
    return {"status": "updated", "message": "RAG 配置已更新"}


@router.get("/settings/available-models")
async def get_available_models() -> dict:
    """List all available LLM models across all providers.

    A model is ``available`` when its provider is enabled and either has a
    configured API key or the router is running in mock mode.
    """
    await _load_setting_section("llm")
    try:
        from app.llm.factory import get_default_router

        mock_mode = get_default_router().mock_mode
    except Exception:
        mock_mode = False

    models = []
    for provider_name, cfg in _settings_store["llm"]["providers"].items():
        if not cfg["enabled"]:
            continue
        has_key = bool(cfg.get("api_key_configured")) or _env_key_available(provider_name)
        for model in cfg["models"]:
            models.append({
                "model": model,
                "provider": provider_name,
                "api_key_configured": has_key,
                "available": has_key or mock_mode,
            })
    return {
        "status": "ok",
        "models": models,
        "total": len(models),
        "default": _settings_store["llm"]["default_model"],
    }


async def reload_llm_router_from_db() -> None:
    """Load persisted LLM settings from MySQL and rebuild the runtime router.

    Called at startup so user-configured providers/keys survive restarts.
    Falls back to agent.yml/.env defaults when nothing is persisted.
    """
    await _load_setting_section("llm")
    try:
        from app.llm.factory import apply_llm_settings

        apply_llm_settings(_settings_store["llm"])
    except Exception:
        logger.warning("Failed to rebuild LLM router from persisted settings", exc_info=True)


def _env_key_available(provider_name: str) -> bool:
    """Whether a built-in provider has a usable key in env / .env (no API
    call involved). Used by available-models so keys configured via .env
    count as configured even when nothing was saved via the settings UI."""
    import os

    from app.config.settings import settings as _settings

    mapping = {
        "openai": ("OPENAI_API_KEY", "openai_api_key"),
        "anthropic": ("ANTHROPIC_API_KEY", "anthropic_api_key"),
        "deepseek": ("DEEPSEEK_API_KEY", "deepseek_api_key"),
        "google": ("GOOGLE_API_KEY", "google_api_key"),
    }
    pair = mapping.get(provider_name)
    if not pair:
        return False
    env_name, attr = pair
    return bool(os.getenv(env_name) or getattr(_settings, attr, None))


@router.delete("/settings/llm/providers/{provider_name}")
async def delete_llm_provider(provider_name: str) -> dict:
    """Delete an LLM provider from the settings."""
    await _load_setting_section("llm")
    provider_name = provider_name.strip().lower()
    llm = _settings_store["llm"]
    if provider_name not in llm["providers"]:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")

    # Prevent deleting built-in providers
    builtin = {"openai", "anthropic", "deepseek", "google"}
    if provider_name in builtin:
        raise HTTPException(status_code=400, detail=f"Cannot delete built-in provider '{provider_name}'. Disable it instead.")

    del llm["providers"][provider_name]
    await _persist_section("llm")
    return {"status": "deleted", "provider": provider_name}
