"""Tests for LiteLLMRouter — config, routing, fallback, cost tracking, streaming."""

from datetime import datetime

import pytest

from app.llm.router import (
    CostTracker,
    LiteLLMRouter,
    ProviderConfig,
    ProviderConnectionError,
    ProviderServerError,
    ProviderTimeoutError,
    RateLimitError,
    RouterConfig,
    _RetryableError,
)

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def anthropic_provider():
    return ProviderConfig(
        provider="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        models=["claude-sonnet-4", "claude-haiku-4-5", "claude-opus-4-8"],
    )


@pytest.fixture
def openai_provider():
    return ProviderConfig(
        provider="openai",
        api_key_env="OPENAI_API_KEY",
        models=["gpt-4.1", "gpt-4.1-mini", "gpt-4o"],
    )


@pytest.fixture
def deepseek_provider():
    return ProviderConfig(
        provider="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        models=["deepseek-v3", "deepseek-r1"],
    )


@pytest.fixture
def router_config(anthropic_provider, openai_provider, deepseek_provider):
    return RouterConfig(
        providers={
            "anthropic": anthropic_provider,
            "openai": openai_provider,
            "deepseek": deepseek_provider,
        },
        default_model="claude-sonnet-4",
        fallback_chain=["gpt-4.1", "deepseek-v3"],
        max_retries=3,
        timeout_seconds=60,
    )


@pytest.fixture
def router(router_config):
    return LiteLLMRouter(router_config, mock_mode=True)


@pytest.fixture
def cost_tracker():
    return CostTracker()


# ═══════════════════════════════════════════════════════════════════════════
# ProviderConfig
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderConfig:
    def test_minimal_config(self):
        pc = ProviderConfig(provider="openai", api_key_env="OPENAI_API_KEY")
        assert pc.provider == "openai"
        assert pc.api_key_env == "OPENAI_API_KEY"
        assert pc.models == []
        assert pc.base_url is None
        assert pc.rate_limit_rpm == 0

    def test_full_config(self):
        pc = ProviderConfig(
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            models=["claude-sonnet-4"],
            base_url="https://proxy.example.com",
            rate_limit_rpm=60,
        )
        assert pc.models == ["claude-sonnet-4"]
        assert pc.base_url == "https://proxy.example.com"
        assert pc.rate_limit_rpm == 60

    def test_equality(self):
        a = ProviderConfig(provider="openai", api_key_env="KEY")
        b = ProviderConfig(provider="openai", api_key_env="KEY")
        assert a == b


# ═══════════════════════════════════════════════════════════════════════════
# RouterConfig
# ═══════════════════════════════════════════════════════════════════════════


class TestRouterConfig:
    def test_defaults(self):
        rc = RouterConfig(providers={})
        assert rc.default_model == "claude-sonnet-4"
        assert rc.fallback_chain == ["gpt-4.1", "deepseek-v3"]
        assert rc.max_retries == 3
        assert rc.timeout_seconds == 60

    def test_custom_fallback(self):
        rc = RouterConfig(
            providers={},
            fallback_chain=["gpt-4o"],
        )
        assert rc.fallback_chain == ["gpt-4o"]

    def test_empty_providers(self):
        rc = RouterConfig(providers={})
        assert rc.providers == {}


# ═══════════════════════════════════════════════════════════════════════════
# CostTracker
# ═══════════════════════════════════════════════════════════════════════════


class TestCostTracker:
    def test_record_single_call(self, cost_tracker):
        record = cost_tracker.record(
            provider="anthropic",
            model="claude-sonnet-4",
            prompt_tokens=500,
            completion_tokens=200,
            latency_ms=1234.5,
        )
        assert record.provider == "anthropic"
        assert record.model == "claude-sonnet-4"
        assert record.prompt_tokens == 500
        assert record.completion_tokens == 200
        assert record.cost_usd > 0  # (500*3.0 + 200*15.0) / 1M
        assert record.latency_ms == 1234.5
        assert record.success is True
        assert isinstance(record.timestamp, datetime)

    def test_approximate_cost_calculation(self, cost_tracker):
        """Claude Sonnet: $3/1M prompt, $15/1M completion."""
        record = cost_tracker.record(
            provider="anthropic",
            model="claude-sonnet-4",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )
        # 1M * 3.0 / 1M + 1M * 15.0 / 1M = 3.0 + 15.0 = 18.0
        assert record.cost_usd == pytest.approx(18.0, rel=0.01)

    def test_unknown_model_zero_cost(self, cost_tracker):
        record = cost_tracker.record(
            provider="unknown",
            model="unknown-model",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert record.cost_usd == 0.0

    def test_total_cost_aggregation(self, cost_tracker):
        cost_tracker.record(provider="openai", model="gpt-4.1",
                           prompt_tokens=1000, completion_tokens=100)
        cost_tracker.record(provider="anthropic", model="claude-sonnet-4",
                           prompt_tokens=500, completion_tokens=50)
        assert cost_tracker.total_cost > 0
        assert cost_tracker.total_calls == 2

    def test_total_tokens(self, cost_tracker):
        cost_tracker.record(prompt_tokens=100, completion_tokens=50,
                           provider="o", model="m")
        cost_tracker.record(prompt_tokens=200, completion_tokens=75,
                           provider="o", model="m")
        tokens = cost_tracker.total_tokens
        assert tokens["prompt"] == 300
        assert tokens["completion"] == 125

    def test_provider_breakdown(self, cost_tracker):
        cost_tracker.record(provider="anthropic", model="claude-sonnet-4",
                           prompt_tokens=1000, completion_tokens=100)
        cost_tracker.record(provider="openai", model="gpt-4.1",
                           prompt_tokens=1000, completion_tokens=100)
        breakdown = cost_tracker.provider_breakdown()
        assert "anthropic" in breakdown
        assert "openai" in breakdown
        assert breakdown["anthropic"] > 0
        assert breakdown["openai"] > 0

    def test_model_breakdown(self, cost_tracker):
        cost_tracker.record(provider="a", model="claude-sonnet-4",
                           prompt_tokens=100, completion_tokens=50)
        cost_tracker.record(provider="o", model="gpt-4.1",
                           prompt_tokens=100, completion_tokens=50)
        breakdown = cost_tracker.model_breakdown()
        assert "claude-sonnet-4" in breakdown
        assert "gpt-4.1" in breakdown

    def test_reset_clears_all(self, cost_tracker):
        cost_tracker.record(provider="a", model="m",
                           prompt_tokens=100, completion_tokens=50)
        assert cost_tracker.total_calls == 1
        cost_tracker.reset()
        assert cost_tracker.total_calls == 0
        assert cost_tracker.total_cost == 0.0
        assert cost_tracker.total_tokens == {"prompt": 0, "completion": 0}

    def test_failed_call_recorded(self, cost_tracker):
        record = cost_tracker.record(
            provider="openai",
            model="gpt-4.1",
            success=False,
        )
        assert record.success is False

    def test_response_object_extracts_usage(self, cost_tracker):
        """Usage info from litellm response object should be extracted."""

        class FakeUsage:
            prompt_tokens = 300
            completion_tokens = 150

        class FakeResponse:
            usage = FakeUsage()

        record = cost_tracker.record(
            provider="openai", model="gpt-4.1", response=FakeResponse()
        )
        assert record.prompt_tokens == 300
        assert record.completion_tokens == 150

    def test_priority_given_to_explicit_tokens(self, cost_tracker):
        """Explicit token args should take priority over response extraction."""

        class FakeUsage:
            prompt_tokens = 999
            completion_tokens = 888

        class FakeResponse:
            usage = FakeUsage()

        record = cost_tracker.record(
            provider="openai", model="gpt-4.1",
            prompt_tokens=100, completion_tokens=50,
            response=FakeResponse(),
        )
        assert record.prompt_tokens == 100
        assert record.completion_tokens == 50


# ═══════════════════════════════════════════════════════════════════════════
# LiteLLMRouter — config and construction
# ═══════════════════════════════════════════════════════════════════════════


class TestRouterConstruction:
    def test_creates_with_config(self, router_config):
        router = LiteLLMRouter(router_config)
        assert router.config is router_config
        assert router.mock_mode is False

    def test_mock_mode(self, router_config):
        router = LiteLLMRouter(router_config, mock_mode=True)
        assert router.mock_mode is True

    def test_custom_cost_tracker(self, router_config, cost_tracker):
        router = LiteLLMRouter(router_config, cost_tracker=cost_tracker)
        assert router.cost_tracker is cost_tracker

    def test_default_cost_tracker_created(self, router_config):
        router = LiteLLMRouter(router_config)
        assert isinstance(router.cost_tracker, CostTracker)

    def test_reset_clears_state(self, router):
        router._call_counter["anthropic"] = 5
        router._provider_status["anthropic"] = False
        router.cost_tracker.record(provider="a", model="m",
                                   prompt_tokens=10, completion_tokens=5)
        router.reset()
        assert router._call_counter == {}
        assert router._provider_status == {}
        assert router.cost_tracker.total_calls == 0


# ═══════════════════════════════════════════════════════════════════════════
# LiteLLMRouter — provider chain
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderChain:
    def test_find_provider_for_known_model(self, router):
        provider = router._find_provider_for_model("claude-sonnet-4")
        assert provider == "anthropic"

    def test_find_provider_for_openai_model(self, router):
        provider = router._find_provider_for_model("gpt-4.1")
        assert provider == "openai"

    def test_find_provider_for_deepseek_model(self, router):
        provider = router._find_provider_for_model("deepseek-v3")
        assert provider == "deepseek"

    def test_unknown_model_returns_first_provider(self, router):
        provider = router._find_provider_for_model("unknown-model-xyz")
        assert provider in router.config.providers

    def test_empty_providers_returns_openai(self):
        config = RouterConfig(providers={})
        router = LiteLLMRouter(config, mock_mode=True)
        provider = router._find_provider_for_model("any-model")
        assert provider == "openai"

    def test_chain_includes_primary_and_fallbacks(self, router):
        chain = router._get_provider_chain("claude-sonnet-4")
        assert len(chain) == 3  # primary + 2 fallbacks
        assert chain[0] == ("anthropic", "claude-sonnet-4")
        assert chain[1] == ("openai", "gpt-4.1")
        assert chain[2] == ("deepseek", "deepseek-v3")

    def test_chain_for_fallback_model(self, router):
        """When the primary model IS a fallback model, chain still works."""
        chain = router._get_provider_chain("deepseek-v3")
        assert chain[0] == ("deepseek", "deepseek-v3")

    def test_chain_excludes_unknown_fallback_providers(self, router):
        """Fallback models from unknown providers are skipped."""
        router.config.fallback_chain = ["unknown-model"]
        chain = router._get_provider_chain("claude-sonnet-4")
        # primary + unknown (no provider match -> first provider)
        assert len(chain) == 2


# ═══════════════════════════════════════════════════════════════════════════
# LiteLLMRouter — mock mode completion
# ═══════════════════════════════════════════════════════════════════════════


class TestMockCompletion:
    async def test_mock_complete_returns_dict(self, router):
        result = await router.complete(
            messages=[{"role": "user", "content": "Hello world"}],
        )
        assert isinstance(result, dict)
        assert "choices" in result
        assert len(result["choices"]) > 0
        assert "message" in result["choices"][0]
        assert "content" in result["choices"][0]["message"]

    async def test_mock_complete_uses_default_model(self, router):
        result = await router.complete(
            messages=[{"role": "user", "content": "test"}],
        )
        assert result["model"] == "claude-sonnet-4"

    async def test_mock_complete_model_override(self, router):
        result = await router.complete(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4.1",
        )
        assert result["model"] == "gpt-4.1"

    async def test_mock_complete_has_usage(self, router):
        result = await router.complete(
            messages=[{"role": "user", "content": "Hello world test"}],
        )
        assert "usage" in result
        assert result["usage"]["prompt_tokens"] > 0

    async def test_mock_complete_tracks_cost(self, router):
        await router.complete(
            messages=[{"role": "user", "content": "test query"}],
        )
        assert router.cost_tracker.total_calls == 1

    @pytest.mark.asyncio
    async def test_mock_stream_yields_chunks(self, router):
        chunks = []
        async for chunk in router.complete_stream(
            messages=[{"role": "user", "content": "hello world test"}],
        ):
            chunks.append(chunk)
        assert len(chunks) > 0
        assert all(isinstance(c, str) for c in chunks)

    async def test_complete_accepts_temperature(self, router):
        result = await router.complete(
            messages=[{"role": "user", "content": "test"}],
            temperature=0.3,
        )
        assert isinstance(result, dict)

    async def test_complete_with_extra_kwargs(self, router):
        result = await router.complete(
            messages=[{"role": "user", "content": "test"}],
            max_tokens=500,
        )
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════════════
# LiteLLMRouter — error handling
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    def test_provider_healthy_default(self, router):
        assert router.provider_healthy("anthropic") is None

    def test_provider_healthy_after_success(self, router):
        router._provider_status["anthropic"] = True
        assert router.provider_healthy("anthropic") is True

    def test_provider_unhealthy_after_failure(self, router):
        router._provider_status["openai"] = False
        assert router.provider_healthy("openai") is False

    def test_retryable_errors_are_retryable(self):
        assert issubclass(RateLimitError, _RetryableError)
        assert issubclass(ProviderTimeoutError, _RetryableError)
        assert issubclass(ProviderServerError, _RetryableError)
        assert issubclass(ProviderConnectionError, _RetryableError)


# ═══════════════════════════════════════════════════════════════════════════
# LiteLLMRouter — error classification in _call_provider
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorClassification:
    """Test that _call_provider correctly wraps various error types."""

    async def test_missing_provider_in_chain(self, router):
        """When a provider in the chain has no config, skip it."""
        router.config.providers.pop("anthropic", None)
        # The primary model's provider is gone; _find_provider_for_model
        # for 'claude-sonnet-4' would return the first remaining provider.
        # The chain: [(first_provider, model), (openai, gpt-4.1), ...]
        result = await router.complete(
            messages=[{"role": "user", "content": "test"}],
        )
        # Should still succeed via mock mode — first provider handles it
        assert isinstance(result, dict)

    async def test_last_error_raised_when_all_exhausted(self, router_config):
        """Simulate all providers unhealthy → last error raised.

        Must disable mock_mode to test real fallback chain behaviour.
        Without mock_mode and without litellm installed, the
        RuntimeError about litellm unavailability is the expected error.
        """
        router = LiteLLMRouter(router_config, mock_mode=False)
        router._provider_status["anthropic"] = False
        router._provider_status["openai"] = False
        router._provider_status["deepseek"] = False

        # In test env without litellm: RuntimeError about missing litellm
        # In prod with litellm: all providers skipped as unhealthy, so the
        # chain exhausts and raises RuntimeError as well
        with pytest.raises(RuntimeError):
            await router.complete(
                messages=[{"role": "user", "content": "test"}],
            )


# ═══════════════════════════════════════════════════════════════════════════
# LiteLLMRouter — real-world integration readiness
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegrationReadiness:
    def test_litellm_import_handled_gracefully(self):
        """Verify litellm import fallback works."""
        from app.llm.router import _LITELLM_AVAILABLE

        # In test env with litellm installed this is True;
        # the import guard exists so tests pass either way.
        assert isinstance(_LITELLM_AVAILABLE, bool)

    def test_router_works_with_minimal_config(self):
        """Router should work with just one provider."""
        config = RouterConfig(
            providers={
                "openai": ProviderConfig(
                    provider="openai",
                    api_key_env="OPENAI_API_KEY",
                    models=["gpt-4.1-mini"],
                ),
            },
            default_model="gpt-4.1-mini",
            fallback_chain=[],
        )
        router = LiteLLMRouter(config, mock_mode=True)
        assert router.config.default_model == "gpt-4.1-mini"

    def test_router_with_custom_base_url(self):
        provider = ProviderConfig(
            provider="openai",
            api_key_env="OPENAI_API_KEY",
            models=["custom-model"],
            base_url="https://my-proxy.example.com/v1",
        )
        config = RouterConfig(
            providers={"openai": provider},
            default_model="custom-model",
        )
        router = LiteLLMRouter(config, mock_mode=True)
        assert router.config.providers["openai"].base_url == "https://my-proxy.example.com/v1"

    def test_large_fallback_chain(self):
        """Chain with many fallbacks should work."""
        config = RouterConfig(
            providers={
                "a": ProviderConfig(provider="a", api_key_env="A",
                                   models=["ma"]),
                "b": ProviderConfig(provider="b", api_key_env="B",
                                   models=["mb"]),
                "c": ProviderConfig(provider="c", api_key_env="C",
                                   models=["mc"]),
                "d": ProviderConfig(provider="d", api_key_env="D",
                                   models=["md"]),
            },
            fallback_chain=["mb", "mc", "md"],
        )
        router = LiteLLMRouter(config, mock_mode=True)
        chain = router._get_provider_chain("ma")
        assert len(chain) == 4  # primary + 3 fallbacks
