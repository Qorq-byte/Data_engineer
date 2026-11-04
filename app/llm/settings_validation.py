"""Validate persisted routing settings before defaults or credentials are consumed."""

def _invalid() -> ValueError:
    # Values and provider names may contain secrets. Never interpolate them.
    return ValueError("Invalid LLM settings structure")


def validate_llm_settings(value: object) -> None:
    """Check recognized fields without mutating records or interpreting legacy fields.

    Missing optional fields retain the caller's defaults. Existing nullable text
    and model-list fields remain nullable; boolean and numeric fields are strict.
    Disabled and keyless providers are checked too, before routing can skip them.
    """
    if not isinstance(value, dict):
        raise _invalid()
    for field in ("default_model", "fallback_model"):
        if value.get(field) is not None and not isinstance(value[field], str):
            raise _invalid()
    limit = value.get("max_tokens", 4096)
    if type(limit) is not int or not 1 <= limit <= 32768:
        raise _invalid()
    providers = value.get("providers", {})
    if not isinstance(providers, dict):
        raise _invalid()
    for name, provider in providers.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(provider, dict):
            raise _invalid()
        for field in ("enabled", "api_key_configured"):
            if field in provider and type(provider[field]) is not bool:
                raise _invalid()
        models = provider.get("models")
        if models is not None and (
            not isinstance(models, list) or any(not isinstance(model, str) for model in models)
        ):
            raise _invalid()
        for field in ("api_key_enc", "base_url"):
            if provider.get(field) is not None and not isinstance(provider[field], str):
                raise _invalid()
        if "rate_limit_rpm" in provider:
            rpm = provider["rate_limit_rpm"]
            if type(rpm) is not int or rpm < 0:
                raise _invalid()
