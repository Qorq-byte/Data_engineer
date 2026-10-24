"""LiteLLM Router — multi-provider LLM access with automatic fallback.

See SPEC §4.10 and implementation-plan §4.10 for the full design.

Phase 2 MVP includes:
  - RouterConfig / ProviderConfig models
  - LiteLLMRouter with complete() / complete_stream()
  - Fallback chain (3 tiers)
  - CostTracker with per-call recording
  - Mock provider support for testing without API keys
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.llm.router import (
        CostRecord,
        CostTracker,
        LiteLLMRouter,
        ProviderConfig,
        RouterConfig,
    )

__all__ = [
    "CostRecord",
    "CostTracker",
    "LiteLLMRouter",
    "ProviderConfig",
    "RouterConfig",
]


def __getattr__(name: str) -> Any:
    """Pure completion checks must not initialize the optional SDK or its cost map."""
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from app.llm import router

    value = getattr(router, name)
    globals()[name] = value
    return value
