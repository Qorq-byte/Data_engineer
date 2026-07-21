"""LiteLLM Router — multi-provider LLM access with automatic fallback.

See SPEC §4.10 and implementation-plan §4.10 for the full design.

Phase 2 MVP includes:
  - RouterConfig / ProviderConfig models
  - LiteLLMRouter with complete() / complete_stream()
  - Fallback chain (3 tiers)
  - CostTracker with per-call recording
  - Mock provider support for testing without API keys
"""

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
