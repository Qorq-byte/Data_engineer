"""Global admission failures must not become model retries or generated SQL."""

import json
import sqlite3

import pytest

from app.api.query import QueryRequest
from app.config.settings import settings
from app.core.sql_generator import SQLGenerator
from app.harness.mysql_coordination import CoordinationUnavailableError
from app.llm.router import LiteLLMRouter, ProviderConfig, RouterConfig
from app.models.query import SQR
from app.workflow.contracts import QueryCapacityError
from app.workflow.query_service import run_query


@pytest.mark.parametrize("error", [QueryCapacityError, CoordinationUnavailableError])
@pytest.mark.parametrize("strategy", ["temperature", "multi_perspective"])
async def test_candidate_strategies_preserve_shared_admission_failure(monkeypatch, error, strategy):
    router = LiteLLMRouter(RouterConfig(), mock_mode=True)

    async def reject(*args, **kwargs):
        raise error("synthetic shared admission failure")

    monkeypatch.setattr(router, "complete", reject)
    with pytest.raises(error):
        await SQLGenerator(router).generate(
            SQR(raw_text="find orders"), num_candidates=2, strategy=strategy
        )


@pytest.mark.parametrize("error", [QueryCapacityError, CoordinationUnavailableError])
async def test_query_admission_failure_is_not_retried_or_counted_as_a_model_call(
    tmp_path,
    monkeypatch,
    error,
):
    path = tmp_path / "audit.sqlite3"
    monkeypatch.setattr(settings, "llm_audit_path", str(path))
    router = LiteLLMRouter(
        RouterConfig(
            providers={
                "fixture": ProviderConfig(
                    provider="fixture",
                    api_key_env="UNUSED",
                    api_key="synthetic",
                    models=["fixture-model"],
                )
            },
            default_model="fixture-model",
            fallback_chain=[],
            max_retries=3,
        ),
        mock_mode=False,
    )
    checks = []

    async def reject(*args, **kwargs):
        checks.append(True)
        raise error("synthetic shared admission failure")

    async def transport(*args, **kwargs):
        pytest.fail("Admission rejection must precede provider transport")

    monkeypatch.setattr(router, "_wait_rate", reject)
    monkeypatch.setattr(router, "_call_provider", transport)
    with pytest.raises(error):
        await run_query(
            QueryRequest(
                nl_text="find order ids", link_schema=False, run_validation=False, num_candidates=1
            ),
            llm_router=router,
        )
    assert len(checks) == 1
    with sqlite3.connect(path) as connection:
        record = json.loads(connection.execute("SELECT payload FROM llm_usage_audit").fetchone()[0])
    assert record["llm_calls"] == 0
    assert record["usage"]["attempt_count"] == 0
    assert record["outcome"] == "error"
