"""LiteLLM Router — multi-provider LLM routing with automatic fallback.

See SPEC §4.10 for the full specification.

Core responsibility: provide a unified async interface to multiple LLM
providers (Anthropic, OpenAI, DeepSeek, etc.) with automatic fallback,
cost tracking, and streaming support.

Phase 2 MVP:
  - RouterConfig / ProviderConfig
  - LiteLLMRouter with complete() / complete_stream()
  - 3-tier fallback chain (primary → fallback1 → fallback2)
  - CostTracker with per-call recording
  - Mock mode for testing without API keys
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ── LiteLLM integration (optional — graceful degradation) ────────────
try:
    import litellm  # type: ignore[import-untyped]

    _LITELLM_AVAILABLE = True
except ImportError:
    litellm = None  # type: ignore[assignment]
    _LITELLM_AVAILABLE = False


# ── Configuration models ───────────────────────────────────────────────


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider.

    Attributes:
        provider: Provider name (``"openai"``, ``"anthropic"``, ``"deepseek"``, …).
        api_key_env: Environment variable name for the API key.
        models: Models supported by this provider.
        base_url: Optional custom endpoint (proxy / private deployment).
        rate_limit_rpm: Maximum requests per minute (0 = unlimited).
    """

    provider: str
    api_key_env: str
    models: list[str] = field(default_factory=list)
    base_url: str | None = None
    rate_limit_rpm: int = 0


@dataclass
class RouterConfig:
    """Top-level LiteLLM router configuration.

    Attributes:
        providers: Provider name → ``ProviderConfig`` mapping.
        default_model: Primary model for all queries.
        fallback_chain: Ordered list of fallback models tried on failure.
        max_retries: Maximum retries per provider before falling back.
        timeout_seconds: Per-request timeout.
    """

    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    default_model: str = "claude-sonnet-4"
    fallback_chain: list[str] = field(default_factory=lambda: ["gpt-4.1", "deepseek-v3"])
    max_retries: int = 3
    timeout_seconds: int = 60


# ── Cost models ────────────────────────────────────────────────────────


@dataclass
class CostRecord:
    """Per-call cost record."""

    timestamp: datetime
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    success: bool = True


class CostTracker:
    """Track LLM costs per provider, model, and session.

    Pricing is approximate and sourced from public provider pages.
    For exact pricing, use the provider's official billing API.
    """

    # Approximate pricing per 1M tokens: (prompt, completion)
    _PRICING: dict[tuple[str, str], tuple[float, float]] = {
        # Anthropic
        ("anthropic", "claude-sonnet-4-20250514"): (3.0, 15.0),
        ("anthropic", "claude-sonnet-4"): (3.0, 15.0),
        ("anthropic", "claude-opus-4-8"): (15.0, 75.0),
        ("anthropic", "claude-haiku-4-5"): (0.80, 4.0),
        # OpenAI
        ("openai", "gpt-4.1"): (2.0, 8.0),
        ("openai", "gpt-4.1-mini"): (0.40, 1.60),
        ("openai", "gpt-4o"): (2.50, 10.0),
        # DeepSeek
        ("deepseek", "deepseek-v3"): (0.27, 1.10),
        ("deepseek", "deepseek-r1"): (0.55, 2.19),
    }

    def __init__(self) -> None:
        self.records: list[CostRecord] = []
        self._provider_totals: dict[str, float] = defaultdict(float)
        self._model_totals: dict[str, float] = defaultdict(float)

    def record(
        self,
        provider: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        success: bool = True,
        response: Any = None,
    ) -> CostRecord:
        """Record a single LLM call.

        Args:
            provider: Provider name.
            model: Model identifier.
            prompt_tokens: Input token count.
            completion_tokens: Output token count.
            latency_ms: Call latency in ms.
            success: Whether the call succeeded.
            response: Optional litellm response object (extracts usage if available).

        Returns:
            The created ``CostRecord``.
        """
        # Try to extract usage from litellm response object
        if response is not None and hasattr(response, "usage"):
            usage = response.usage
            prompt_tokens = prompt_tokens or getattr(usage, "prompt_tokens", 0)
            completion_tokens = completion_tokens or getattr(
                usage, "completion_tokens", 0
            )

        price = self._PRICING.get((provider, model), (0.0, 0.0))
        cost = (prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000

        record = CostRecord(
            timestamp=datetime.now(UTC),
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=round(cost, 6),
            latency_ms=latency_ms,
            success=success,
        )
        self.records.append(record)
        self._provider_totals[provider] += cost
        self._model_totals[model] += cost
        return record

    @property
    def total_cost(self) -> float:
        """Total accumulated cost in USD."""
        return round(sum(r.cost_usd for r in self.records), 6)

    @property
    def total_calls(self) -> int:
        """Total number of recorded calls."""
        return len(self.records)

    @property
    def total_tokens(self) -> dict[str, int]:
        """Aggregate prompt + completion token counts."""
        prompt = sum(r.prompt_tokens for r in self.records)
        completion = sum(r.completion_tokens for r in self.records)
        return {"prompt": prompt, "completion": completion}

    def provider_breakdown(self) -> dict[str, float]:
        """Cost breakdown by provider."""
        return dict(self._provider_totals)

    def model_breakdown(self) -> dict[str, float]:
        """Cost breakdown by model."""
        return dict(self._model_totals)

    def reset(self) -> None:
        """Clear all records (useful for testing)."""
        self.records.clear()
        self._provider_totals.clear()
        self._model_totals.clear()


# ── Router ─────────────────────────────────────────────────────────────


# Error classes for common LLM call failures
class _RetryableError(Exception):
    """Marker for errors that should trigger fallback."""


class RateLimitError(_RetryableError):
    """HTTP 429 — retry with backoff or fallback."""


class ProviderTimeoutError(_RetryableError):
    """Request exceeded timeout."""


class ProviderServerError(_RetryableError):
    """HTTP 5xx — provider internal error."""


class ProviderConnectionError(_RetryableError):
    """DNS / TCP connection failure."""


class LiteLLMRouter:
    """Unified LLM router — provider registration, routing, fallback, streaming,
    and cost tracking.

    Usage::

        config = RouterConfig(
            providers={
                "anthropic": ProviderConfig(
                    provider="anthropic",
                    api_key_env="ANTHROPIC_API_KEY",
                    models=["claude-sonnet-4"],
                ),
            },
            default_model="claude-sonnet-4",
        )
        router = LiteLLMRouter(config)
        response = await router.complete(
            messages=[{"role": "user", "content": "Hello"}],
        )
    """

    def __init__(
        self,
        config: RouterConfig,
        cost_tracker: CostTracker | None = None,
        *,
        mock_mode: bool = False,
    ) -> None:
        self.config = config
        self.cost_tracker = cost_tracker or CostTracker()
        self.mock_mode = mock_mode

        # Runtime state
        self._provider_status: dict[str, bool] = {}  # True = healthy
        self._call_counter: dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        n: int = 1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Non-streaming completion with automatic fallback.

        Args:
            messages: Chat message list (``role`` + ``content``).
            model: Model override (default: ``config.default_model``).
            temperature: Sampling temperature (0.0–1.0).
            n: Number of completions to generate.
            **kwargs: Passed to the underlying provider.

        Returns:
            A dict with keys ``"choices"``, ``"model"``, ``"usage"``,
            ``"provider"``.
        """
        model = model or self.config.default_model
        return await self._route_with_fallback(
            messages, model, temperature, n, stream=False, **kwargs
        )

    async def complete_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Streaming completion — yields text deltas.

        Args:
            messages: Chat message list.
            model: Model override.
            temperature: Sampling temperature.
            **kwargs: Passed to the underlying provider.

        Yields:
            Text chunks as they arrive from the LLM.
        """
        model = model or self.config.default_model
        gen = await self._route_with_fallback(
            messages, model, temperature, 1, stream=True, **kwargs
        )
        async for chunk in gen:
            yield chunk

    def provider_healthy(self, provider_name: str) -> bool | None:
        """Check whether *provider_name* is currently considered healthy.

        Returns ``None`` if unknown (not yet contacted).
        """
        return self._provider_status.get(provider_name)

    def reset(self) -> None:
        """Reset runtime state (counters, status). Useful for testing."""
        self._provider_status.clear()
        self._call_counter.clear()
        self.cost_tracker.reset()

    # ------------------------------------------------------------------
    # Internal — routing
    # ------------------------------------------------------------------

    async def _route_with_fallback(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        n: int,
        stream: bool,
        **kwargs: Any,
    ) -> Any:
        """Try the primary model, then each fallback in order.

        Returns the first successful response or raises the last error.
        """
        # Mock mode: skip provider resolution entirely
        if self.mock_mode:
            t0 = time.perf_counter()
            dummy = ProviderConfig(provider="mock", api_key_env="")
            response = await self._mock_call(dummy, model, messages, n, stream)
            latency = (time.perf_counter() - t0) * 1000
            self.cost_tracker.record(
                provider="mock",
                model=model,
                latency_ms=latency,
                response=response if not stream else None,
            )
            return response

        chain = self._get_provider_chain(model)
        last_error: Exception | None = None

        for provider_name, provider_model in chain:
            provider = self.config.providers.get(provider_name)
            if provider is None:
                last_error = ValueError(
                    f"Provider '{provider_name}' not in config"
                )
                continue

            # Skip providers known to be unhealthy
            if self._provider_status.get(provider_name) is False:
                continue

            try:
                t0 = time.perf_counter()
                response = await self._call_provider(
                    provider, provider_model, messages,
                    temperature, n, stream, **kwargs
                )
                latency = (time.perf_counter() - t0) * 1000

                # Record success
                self._provider_status[provider_name] = True
                self._call_counter[provider_name] += 1
                self.cost_tracker.record(
                    provider=provider_name,
                    model=provider_model,
                    latency_ms=latency,
                    response=response if not stream else None,
                )

                return response

            except _RetryableError as e:
                self._provider_status[provider_name] = False
                last_error = e
                continue
            except Exception:
                # Non-retryable — record and re-raise immediately
                self.cost_tracker.record(
                    provider=provider_name,
                    model=provider_model,
                    success=False,
                )
                raise

        # All providers exhausted
        raise last_error or RuntimeError(
            f"All providers exhausted for model '{model}'"
        )

    def _get_provider_chain(self, model: str) -> list[tuple[str, str]]:
        """Build the fallback chain for *model*.

        Returns a list of (provider_name, model) pairs:
        primary → fallback[0] → fallback[1] → …
        """
        primary = self._find_provider_for_model(model)
        chain: list[tuple[str, str]] = [(primary, model)]
        for fb_model in self.config.fallback_chain:
            fb_provider = self._find_provider_for_model(fb_model)
            chain.append((fb_provider, fb_model))
        return chain

    def _find_provider_for_model(self, model: str) -> str:
        """Find which configured provider owns *model*.

        If no provider lists the model explicitly, returns ``"openai"``
        as a reasonable default (LiteLLM passes through unknown models
        via the OpenAI-compatible endpoint).
        """
        for name, pc in self.config.providers.items():
            if model in pc.models:
                return name
        # Fall back to the first configured provider, or "openai"
        if self.config.providers:
            return next(iter(self.config.providers))
        return "openai"

    # ------------------------------------------------------------------
    # Internal — provider call
    # ------------------------------------------------------------------

    async def _call_provider(
        self,
        provider: ProviderConfig,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        n: int,
        stream: bool,
        **kwargs: Any,
    ) -> Any:
        """Execute a single LLM call through the configured provider.

        Handles:
          - Mock mode (returns synthetic response)
          - LiteLLM integration (if installed)
          - API key resolution from env vars
          - Timeout enforcement
          - Error classification (retryable vs fatal)
        """
        if self.mock_mode:
            return await self._mock_call(provider, model, messages, n, stream)

        if not _LITELLM_AVAILABLE:
            raise RuntimeError(
                "litellm is not installed. Install with: pip install litellm"
            )

        api_key = os.getenv(provider.api_key_env, "")
        litellm_model = f"{provider.provider}/{model}"

        # Build call kwargs
        call_kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": messages,
            "temperature": temperature,
            "n": n,
            "api_key": api_key or None,
            "timeout": self.config.timeout_seconds,
        }
        if provider.base_url:
            call_kwargs["api_base"] = provider.base_url
        call_kwargs.update(kwargs)

        try:
            if stream:
                response = await litellm.acompletion(stream=True, **call_kwargs)
                return self._stream_to_async_gen(response)
            else:
                return await litellm.acompletion(**call_kwargs)
        except litellm.exceptions.RateLimitError as e:
            raise RateLimitError(f"Rate limited by {provider.provider}") from e
        except litellm.exceptions.Timeout as e:
            raise ProviderTimeoutError(f"Timeout from {provider.provider}") from e
        except litellm.exceptions.APIConnectionError as e:
            raise ProviderConnectionError(str(e)) from e
        except litellm.exceptions.ServiceUnavailableError as e:
            raise ProviderServerError(str(e)) from e
        except litellm.exceptions.APIError as e:
            # 4xx errors are non-retryable
            if hasattr(e, "status_code") and 400 <= e.status_code < 500:
                raise
            raise ProviderServerError(str(e)) from e
        except TimeoutError as e:
            raise ProviderTimeoutError(f"Timeout from {provider.provider}") from e

    @staticmethod
    async def _stream_to_async_gen(response: Any) -> AsyncIterator[str]:
        """Convert a litellm streaming response to an async generator of text chunks."""
        async for chunk in response:
            if (
                chunk.choices
                and chunk.choices[0].delta
                and chunk.choices[0].delta.content
            ):
                yield chunk.choices[0].delta.content

    async def _mock_call(
        self,
        provider: ProviderConfig,
        model: str,
        messages: list[dict[str, str]],
        n: int = 1,
        stream: bool = False,
    ) -> Any:
        """Return a synthetic response for testing without API keys."""
        user_text = ""
        for m in messages:
            if m.get("role") == "user":
                user_text = m.get("content", "")
                break

        if stream:
            async def _mock_stream():
                for word in user_text.split():
                    yield word + " "

            return _mock_stream()

        # Generate n choices — each slightly different
        n = max(1, n)
        choices = []
        for i in range(n):
            choices.append({
                "index": i,
                "message": {
                    "role": "assistant",
                    "content": (
                        f"[mock response from {provider.provider}/{model}]"
                        if i == 0
                        else f"[mock response {i+1} from {provider.provider}/{model}]"
                    ),
                },
                "finish_reason": "stop",
            })

        return {
            "id": f"mock-{n:03d}",
            "choices": choices,
            "model": model,
            "usage": {
                "prompt_tokens": len(user_text) // 4,
                "completion_tokens": 20 * n,
                "total_tokens": len(user_text) // 4 + 20 * n,
            },
        }
