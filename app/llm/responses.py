"""Normalize SDK responses at the native transport boundary, without importing SDKs."""

from typing import Any

_ERROR = "Non-streaming model response must be an object"


def normalize_completion(response: object) -> dict[str, Any]:
    """Keep dictionary compatibility; accept structured SDK model serialization.

    Preserve completion markers, refusal/tool data and usage for downstream guards.
    Do not turn arbitrary strings into SQL or expose serializer exception text.
    """
    if isinstance(response, dict):
        return response
    try:
        dump = getattr(response, "model_dump", None)
        if not callable(dump):
            raise TypeError(_ERROR)
        result = dump()
    except Exception:
        raise TypeError(_ERROR) from None
    if not isinstance(result, dict):
        raise TypeError(_ERROR)
    return result
