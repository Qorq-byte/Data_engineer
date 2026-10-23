"""Output request policy must reach the actual router without inventing routing modes."""

import copy
from types import SimpleNamespace

import pytest

from app.api import create_app
from app.api import settings as api_settings
from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.llm.budget import active_budget
from app.llm.factory import build_router_from_settings
from app.llm.router import LiteLLMRouter, ProviderConfig, ProviderTimeoutError, RouterConfig
from tests.api_client import authenticated_client
from tests.test_api import test_llm_persistence as persistence_tests

stored = persistence_tests.stored


def test_saved_output_cap_reaches_router():
    router = build_router_from_settings({"max_tokens": 27, "providers": {}})
    assert getattr(router.config, "max_output_tokens", None) == 27


@pytest.mark.parametrize("payload", [{"temperature": 0.8}, {"routing_strategy": "cost_first"}])
def test_unsupported_policy_writes_rejected(payload):
    response = authenticated_client(create_app()).put("/api/v1/settings/llm", json=payload)
    assert response.status_code == 422


@pytest.fixture
def transport(monkeypatch):
    import app.llm.router as module

    calls = []

    async def complete(**options):
        calls.append(options)
        if options.get("stream"):

            async def chunks():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))]
                )

            return chunks()
        return {"choices": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

    monkeypatch.setattr(module, "_LITELLM_AVAILABLE", True)
    monkeypatch.setattr(module.litellm, "acompletion", complete)
    current = LiteLLMRouter(
        RouterConfig(
            providers={
                "openai": ProviderConfig(
                    "openai", "", api_key="synthetic-token", models=["gpt-4.1"]
                )
            },
            default_model="gpt-4.1",
            fallback_chain=[],
            max_retries=0,
            max_output_tokens=17,
        )
    )
    return current, calls


@pytest.mark.parametrize(
    "options,key,expected",
    [
        ({}, "max_tokens", 17),
        ({"max_tokens": 3}, "max_tokens", 3),
        ({"max_tokens": 99}, "max_tokens", 17),
        ({"max_tokens": None}, "max_tokens", 17),
        ({"max_completion_tokens": 2}, "max_completion_tokens", 2),
        ({"max_completion_tokens": 99}, "max_completion_tokens", 17),
        ({"max_tokens": None, "max_completion_tokens": 6}, "max_completion_tokens", 6),
    ],
)
async def test_actual_transport_receives_cap_without_changing_temperature(
    transport, options, key, expected
):
    current, calls = transport
    await current.complete([], temperature=0.3, n=2, **options)
    assert calls[0][key] == expected
    assert calls[0]["temperature"] == 0.3 and calls[0]["n"] == 2
    assert calls[0]["num_retries"] == 0
    assert ("max_completion_tokens" if key == "max_tokens" else "max_tokens") not in calls[0]


async def test_actual_stream_transport_receives_cap(transport):
    current, calls = transport
    assert [chunk async for chunk in current.complete_stream([], max_completion_tokens=100)] == [
        "ok"
    ]
    assert calls[0]["stream"] is True and calls[0]["max_completion_tokens"] == 17


@pytest.mark.parametrize(
    "options",
    [
        {"max_tokens": True},
        {"max_tokens": 0},
        {"max_tokens": -1},
        {"max_tokens": 1.5},
        {"max_tokens": "3"},
        {"max_completion_tokens": False},
        {"max_tokens": 2, "max_completion_tokens": 3},
        {"max_new_tokens": 3},
        {"extra_body": {"max_tokens": 999}},
    ],
)
async def test_invalid_options_rejected_before_budget_or_transport(transport, options):
    current, calls = transport
    budget = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=2))
    token = active_budget.set(budget)
    try:
        with pytest.raises(ValueError):
            await current.complete([], **options)
        assert budget.llm_calls_used == 0 and not calls
    finally:
        active_budget.reset(token)


async def test_retry_and_fallback_keep_cap_and_physical_budget(transport, monkeypatch):
    current, calls = transport
    import app.llm.router as module

    current.config.max_retries = 1
    current.config.retry_backoff_seconds = 0
    current.config.providers["other"] = ProviderConfig(
        "other",
        "",
        api_key="synthetic-key",
        base_url="https://example.invalid/v1",
        models=["fallback"],
    )
    current.config.fallback_chain = ["fallback"]
    original = module.litellm.acompletion

    async def fail_primary(**options):
        if options["model"] == "openai/gpt-4.1":
            calls.append(options)
            raise ProviderTimeoutError("synthetic failure")
        return await original(**options)

    monkeypatch.setattr(module.litellm, "acompletion", fail_primary)
    budget = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=3))
    token = active_budget.set(budget)
    try:
        await current.complete([], max_tokens=999)
        assert len(calls) == budget.llm_calls_used == 3
        assert all(options["max_tokens"] == 17 for options in calls)
    finally:
        active_budget.reset(token)


def test_independent_router_can_remain_uncapped():
    current = LiteLLMRouter(RouterConfig())
    assert current._completion_options({}) == {}
    assert current._completion_options({"max_tokens": 100000}) == {"max_tokens": 100000}


@pytest.mark.parametrize("bad", [True, 0, -1, 32769, "4096", 3.5, None])
def test_invalid_stored_cap_rejected_by_factory(bad):
    with pytest.raises(ValueError):
        build_router_from_settings({"max_tokens": bad, "providers": {}})


@pytest.mark.parametrize(
    "payload",
    [
        {"max_tokens": True},
        {"max_tokens": "20"},
        {"max_tokens": None},
        {"max_tokens": 1.5},
        {"max_tokens": 0},
        {"max_tokens": 32769},
        {"max_tokens": 20, "temperature": None},
        {"max_tokens": 20, "routing_strategy": None},
    ],
)
async def test_invalid_api_write_has_no_partial_mutation(stored, payload):
    before = await stored.get_setting("llm")
    memory = copy.deepcopy(api_settings._settings_store)
    response = authenticated_client(create_app()).put("/api/v1/settings/llm", json=payload)
    assert response.status_code == 422
    assert await stored.get_setting("llm") == before
    assert api_settings._settings_store == memory


@pytest.mark.parametrize("limit", [1, 32768])
async def test_real_persistence_restore_and_legacy_fields_ignored(stored, limit):
    from app.llm import factory

    raw = await stored.get_setting("llm")
    raw.update(routing_strategy="round_robin", temperature=1.8)
    await stored.save_setting("llm", raw)
    result = await api_settings.update_llm_settings(
        api_settings.LLMSettingsUpdate(max_tokens=limit)
    )
    assert result["max_tokens"] == result["runtime"]["max_output_tokens"] == limit
    assert result["routing_policy"] == "primary_then_fallback"
    assert result["temperature_policy"] == "caller_supplied"
    assert "routing_strategy" not in result and "temperature" not in result
    assert (await stored.get_setting("llm"))["temperature"] == 1.8
    factory.reset_default_router()
    before = await api_settings.get_llm_settings()
    assert before["runtime"]["max_output_tokens"] is None
    await api_settings.reload_llm_router_from_db()
    assert factory.get_default_router().config.max_output_tokens == limit


async def test_get_reports_configured_runtime_drift_without_applying(stored):
    from app.llm import factory

    current = factory.get_default_router()
    raw = await stored.get_setting("llm")
    raw["max_tokens"] = 29
    await stored.save_setting("llm", raw)
    view = await api_settings.get_llm_settings()
    assert view["max_tokens"] == 29
    assert view["runtime"]["max_output_tokens"] == 4096
    assert factory.get_default_router() is current
