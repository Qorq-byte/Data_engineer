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

import asyncio
import contextlib
import os
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.llm.completion import IncompleteCompletionError, check_completion, response_field

# ── LiteLLM integration (optional — graceful degradation) ────────────
try:
    import litellm

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
    api_key: str | None = None


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
    timeout_seconds: float = 60
    max_concurrency: int = 4
    retry_backoff_seconds: float = 0.1
    cooldown_seconds: float = 5
    max_output_tokens: int | None = None


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
        self._total_calls = 0
        self._total_cost = 0.0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
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
            completion_tokens = completion_tokens or getattr(usage, "completion_tokens", 0)

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
        del self.records[:-1000]
        self._total_calls += 1
        self._total_cost += record.cost_usd
        self._total_prompt_tokens += prompt_tokens
        self._total_completion_tokens += completion_tokens
        self._provider_totals[provider] += cost
        self._model_totals[model] += cost
        return record

    @property
    def total_cost(self) -> float:
        """Total accumulated cost in USD."""
        return round(self._total_cost, 6)

    @property
    def total_calls(self) -> int:
        """Total number of recorded calls."""
        return self._total_calls

    @property
    def total_tokens(self) -> dict[str, int]:
        """Aggregate prompt + completion token counts."""
        prompt = self._total_prompt_tokens
        completion = self._total_completion_tokens
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
        self._total_calls = 0
        self._total_cost = 0.0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
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


class ProviderConfigurationError(Exception):
    """Provider misconfigured (missing key / base_url) — non-retryable.

    Raised instead of attempting a call: retrying cannot fix a missing
    configuration, and falling back to another provider would mask the
    user's setup mistake.
    """


# Provider names LiteLLM understands natively (``{name}/{model}`` routing).
# Anything else is treated as a custom OpenAI-compatible endpoint and
# requires an explicit ``base_url``.
_LITELLM_KNOWN_PROVIDERS: frozenset[str] = frozenset(
    {
        "openai",
        "anthropic",
        "deepseek",
        "google",
        "gemini",
        "azure",
        "azure_ai",
        "bedrock",
        "vertex_ai",
        "cohere",
        "mistral",
        "groq",
        "qwen",
        "together_ai",
        "ollama",
        "xai",
        "fireworks_ai",
        "perplexity",
        "moonshot",
        "zhipu",
        "openrouter",
        "deepinfra",
    }
)


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
        self._retry_after: dict[str, float] = {}
        self._rate_windows: dict[str, deque[float]] = defaultdict(deque)

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
        response = await self._route_with_fallback(
            messages, model, temperature, n, stream=False, **kwargs
        )
        if not isinstance(response, dict):
            raise TypeError("Non-streaming model response must be an object")
        return response

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
        try:
            async for chunk in gen:
                yield chunk
        finally:
            if hasattr(gen, "aclose"):
                await gen.aclose()

    def provider_healthy(self, provider_name: str) -> bool | None:
        """Check whether *provider_name* is currently considered healthy.

        Returns ``None`` if unknown (not yet contacted).
        """
        return self._provider_status.get(provider_name)

    def reset(self) -> None:
        """Reset runtime state (counters, status). Useful for testing."""
        self._provider_status.clear()
        self._retry_after.clear()
        self._rate_windows.clear()
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
        from app.llm.budget import reserve_llm_call

        kwargs = self._completion_options(kwargs)

        # Mock mode: skip provider resolution entirely
        if self.mock_mode:
            t0 = time.perf_counter()
            reserve_llm_call()
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
                last_error = ValueError(f"Provider '{provider_name}' not in config")
                continue

            # Retry only after cooldown; a later request probes the provider again.
            if self._provider_status.get(
                provider_name
            ) is False and time.monotonic() < self._retry_after.get(provider_name, float("inf")):
                continue
            for attempt in range(min(max(0, self.config.max_retries), 3) + 1):
                try:
                    await self._wait_rate(provider_name, provider.rate_limit_rpm)
                    t0 = time.perf_counter()
                    response = await self._limited_call(
                        provider, provider_model, messages, temperature, n, stream, **kwargs
                    )
                    latency = (time.perf_counter() - t0) * 1000
                    self._provider_status[provider_name] = True
                    self._retry_after.pop(provider_name, None)
                    self._call_counter[provider_name] += 1
                    self.cost_tracker.record(
                        provider=provider_name,
                        model=provider_model,
                        latency_ms=latency,
                        response=response if not stream else None,
                    )
                    return response
                except _RetryableError as exc:
                    last_error = exc
                    self.cost_tracker.record(
                        provider=provider_name, model=provider_model, success=False
                    )
                    if attempt < min(max(0, self.config.max_retries), 3):
                        await asyncio.sleep(min(2, self.config.retry_backoff_seconds * 2**attempt))
                        continue
                    self._provider_status[provider_name] = False
                    self._retry_after[provider_name] = time.monotonic() + max(
                        0, self.config.cooldown_seconds
                    )
                except Exception:
                    self.cost_tracker.record(
                        provider=provider_name, model=provider_model, success=False
                    )
                    raise

        # All providers exhausted
        raise last_error or RuntimeError(f"All providers exhausted for model '{model}'")

    def _completion_options(self, options: dict[str, Any]) -> dict[str, Any]:
        """Bound supported output options before budgeting or transport.

        This is an output request parameter, not a total query/cost budget.
        Provider acceptance and actual output usage require live evaluation.
        """
        result = dict(options)
        aliases = ("max_tokens", "max_completion_tokens")
        cap = self.config.max_output_tokens
        if cap is not None and (type(cap) is not int or not 1 <= cap <= 32768):
            raise ValueError("Invalid configured output token limit")
        extra = result.get("extra_body")
        if isinstance(extra, dict) and any(key in extra for key in (*aliases, "max_new_tokens")):
            raise ValueError("Output token overrides in extra_body are not supported")
        if "max_new_tokens" in result:
            raise ValueError("Use max_tokens or max_completion_tokens")
        present = []
        for key in aliases:
            if result.get(key) is None:
                result.pop(key, None)
            else:
                value = result[key]
                if type(value) is not int or value <= 0:
                    raise ValueError("Output token limit must be a positive integer")
                present.append(key)
        if len(present) > 1:
            raise ValueError("Use only one output token limit parameter")
        if cap is not None:
            if present:
                key = present[0]
                result[key] = min(result[key], cap)
            else:
                result["max_tokens"] = cap
        return result

    async def _wait_rate(self, provider_name: str, rpm: int) -> None:
        if rpm <= 0:
            return
        window = self._rate_windows[provider_name]
        while True:
            now = time.monotonic()
            while window and window[0] <= now - 60:
                window.popleft()
            if len(window) < rpm:
                window.append(now)
                return
            await asyncio.sleep(max(0, window[0] + 60 - now))

    async def _limited_call(
        self,
        provider: ProviderConfig,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        n: int,
        stream: bool,
        **kwargs: Any,
    ) -> Any:
        from app.db.runtime import gate

        semaphore = gate("llm-calls", max(1, self.config.max_concurrency))
        await semaphore.acquire()
        handed_to_stream = False
        try:
            from app.llm.budget import reserve_llm_call

            reserve_llm_call()
            try:
                response = await asyncio.wait_for(
                    self._call_provider(
                        provider, model, messages, temperature, n, stream, **kwargs
                    ),
                    self.config.timeout_seconds,
                )
            except TimeoutError as exc:
                raise ProviderTimeoutError("Provider deadline exceeded") from exc
            if not stream:
                return response

            async def guarded_stream() -> AsyncIterator[str]:
                try:
                    async with asyncio.timeout(self.config.timeout_seconds):
                        async for chunk in response:
                            yield chunk
                finally:
                    semaphore.release()
                    if hasattr(response, "aclose"):
                        with contextlib.suppress(Exception):
                            await response.aclose()

            handed_to_stream = True
            return guarded_stream()
        finally:
            if not handed_to_stream:
                semaphore.release()

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
            raise RuntimeError("litellm is not installed. Install with: pip install litellm")

        # ── Key resolution: explicit (settings-provided) key wins ──
        api_key = provider.api_key or os.getenv(provider.api_key_env, "") or None
        if not api_key:
            raise ProviderConfigurationError(
                f"Provider '{provider.provider}' has no API key configured."
            )

        # ── Provider routing ─────────────────────────────────────────
        # Known LiteLLM provider → ``{provider}/{model}``. Custom provider →
        # OpenAI-compatible endpoint (requires base_url).
        if provider.provider in _LITELLM_KNOWN_PROVIDERS:
            litellm_provider = provider.provider
        else:
            if not provider.base_url:
                raise ProviderConfigurationError(
                    f"Custom provider '{provider.provider}' requires a base_url "
                    "(OpenAI-compatible endpoint)."
                )
            litellm_provider = "openai"

        litellm_model = f"{litellm_provider}/{model}"

        # Build call kwargs
        call_kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": messages,
            "temperature": temperature,
            "n": n,
            "api_key": api_key,
            "timeout": self.config.timeout_seconds,
            "num_retries": 0,  # Harness budgets all physical retry attempts
        }
        if provider.base_url:
            call_kwargs["api_base"] = provider.base_url
        call_kwargs.update(kwargs)
        call_kwargs = self._completion_options(call_kwargs)
        call_kwargs["num_retries"] = 0

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
        except litellm.exceptions.InternalServerError as e:
            # 5xx from the provider or a wrapped connection failure —
            # retryable, so the fallback chain can take over.
            raise ProviderServerError(str(e)) from e
        except litellm.exceptions.APIError as e:
            # 4xx errors are non-retryable
            if hasattr(e, "status_code") and 400 <= e.status_code < 500:
                raise
            raise ProviderServerError(str(e)) from e
        except TimeoutError as e:
            raise ProviderTimeoutError(f"Timeout from {provider.provider}") from e
        except Exception as e:
            # Safety net: some providers surface transport failures as a
            # generic exception. Classify connection-ish failures as
            # retryable so fallback / circuit-breaking actually engages.
            msg = str(e).lower()
            if any(
                token in msg
                for token in (
                    "connection error",
                    "connection refused",
                    "connect error",
                    "connection reset",
                    "max retries exceeded",
                    "timed out",
                )
            ):
                raise ProviderConnectionError(str(e)) from e
            raise

    @staticmethod
    async def _stream_to_async_gen(response: Any) -> AsyncIterator[str]:
        """Yield provisional text, requiring a normal terminal marker before success."""
        finished = False
        try:
            async for chunk in response:
                choices = response_field(chunk, "choices")
                if not choices:  # usage-only chunks are allowed, including after stop
                    continue
                choice = choices[0]
                check_completion(choice)
                content = response_field(response_field(choice, "delta"), "content")
                reason = response_field(choice, "finish_reason")
                if content is not None and not isinstance(content, str):
                    raise IncompleteCompletionError("Model stream contained non-text output.")
                if finished and (content or reason is not None):
                    raise IncompleteCompletionError(
                        "Model stream continued after its completion marker; SQL discarded."
                    )
                if content:
                    yield content
                if reason == "stop":
                    finished = True
            if not finished:
                raise IncompleteCompletionError(
                    "Model stream ended without a completion marker; SQL discarded."
                )
        finally:
            if hasattr(response, "aclose"):
                with contextlib.suppress(Exception):
                    await response.aclose()

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

            async def _mock_stream() -> AsyncIterator[str]:
                for word in user_text.split():
                    yield word + " "

            return _mock_stream()

        # Generate n choices — each slightly different
        n = max(1, n)
        choices = []
        for i in range(n):
            choices.append(
                {
                    "index": i,
                    "message": {
                        "role": "assistant",
                        "content": (
                            f"[mock response from {provider.provider}/{model}]"
                            if i == 0
                            else f"[mock response {i + 1} from {provider.provider}/{model}]"
                        ),
                    },
                    "finish_reason": "stop",
                }
            )

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
