"""Single-process worker for the synthetic profile; never initialized by prepare."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.audit.store import UsageAuditStore
from app.evaluation.artifacts import write_atomic
from app.evaluation.model_batch import (
    LIMITS,
    V2_PROFILE,
    manifest_digest,
    profile_dataset,
    profile_fixture,
)
from app.evaluation.predictions import export_predictions
from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.llm.budget import active_call_guard, active_call_limits
from app.llm.router import LiteLLMRouter, ProviderConfig, RouterConfig


class ControlledRouter(LiteLLMRouter):
    """Retain native accounting; redact provider errors before workflow persistence."""

    async def _call_provider(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await super()._call_provider(*args, **kwargs)
        except Exception:
            raise RuntimeError("Controlled model transport failed") from None


def approved_guard(provider: ProviderConfig, model: str) -> Callable[..., None]:
    def guard(
        actual: object, requested: str, messages: list[dict[str, str]], n: int,
        output_tokens: int | None, stream: bool, overrides: dict[str, Any],
    ) -> None:
        checks = {
            "provider": actual is provider, "model": requested == model, "n": n == 1,
            "stream": not stream, "output": output_tokens == LIMITS["max_output_tokens"],
            # Native routing inserts its configured cap before reaching this boundary.
            "overrides": overrides in ({}, {"max_tokens": LIMITS["max_output_tokens"]}),
        }
        for name, passed in checks.items():
            if not passed:
                raise RuntimeError(f"Controlled model policy rejected {name}")
        if len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) > LIMITS["message_bytes"]:
            raise RuntimeError("Controlled model message limit exceeded")
    return guard


async def run_batch(manifest: dict[str, Any], api_key: str, directory: Path) -> dict[str, Any]:
    """Caller creates a fresh isolated workspace and settings before importing this module."""
    from app.api.query import QueryRequest
    from app.config.settings import settings
    from app.db.connections import ConnectionFactory, DatabaseType
    from app.workflow.query_service import run_query
    from scripts.evaluate_quality import evaluate_sql

    dataset = profile_dataset(manifest["profile"])
    if ConnectionFactory.list_all():
        raise ValueError("Evaluation requires a fresh process without database connections")
    audit_path = directory / "usage.sqlite3"
    if (settings.llm_audit_path != str(audit_path)
            or not settings.llm_audit_candidate_digests):
        raise ValueError("Evaluation audit isolation is not configured")
    provider = ProviderConfig("deepseek", "DEEPSEEK_API_KEY", [manifest["model"]],
                              base_url=manifest["base_url"], api_key=api_key)
    router = ControlledRouter(RouterConfig(
        providers={"deepseek": provider}, default_model=manifest["model"], fallback_chain=[],
        max_retries=0, timeout_seconds=LIMITS["request_seconds"], max_concurrency=1,
        max_output_tokens=LIMITS["max_output_tokens"],
    ))
    batch = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=LIMITS["max_calls"]))
    guard_token = active_call_guard.set(approved_guard(provider, manifest["model"]))
    db_id = "model-eval-fixture"
    responses: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "profile": manifest["profile"], "manifest_sha256": manifest_digest(manifest),
        "model": manifest["model"], "limits": manifest["limits"], "cases": states,
        "physical_calls_reserved": 0, "actual_cost_usd": None, "evaluation": None,
        "status": "running", "measurement_scope": manifest["scope"],
    }

    def checkpoint() -> None:
        report["physical_calls_reserved"] = batch.llm_calls_used
        write_atomic(directory / "responses.json", responses)
        write_atomic(directory / "bindings.json", bindings)
        write_atomic(directory / "report.json", report)

    deadline = time.monotonic() + LIMITS["batch_seconds"]
    try:
        conn = await ConnectionFactory.create(DatabaseType.SQLITE,
            {"path": str(directory / "fixture.sqlite3")}, db_id)
        conn.executescript(profile_fixture(manifest["profile"]))
        conn.execute("PRAGMA query_only=ON")
        for case in manifest["cases"]:
            state = {"id": case["id"], "status": "unattempted"}
            states.append(state)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                continue
            own = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1))
            token = active_call_limits.set((batch, own))
            try:
                state["status"] = "running"
                checkpoint()
                async with asyncio.timeout(min(remaining, LIMITS["case_seconds"])):
                    response = await run_query(QueryRequest(
                        nl_text=case["question"], session_id=f"eval-{uuid.uuid4().hex}",
                        domain="", database=db_id, dialect="sqlite", num_candidates=1,
                        execute=False, run_validation=True, model=manifest["model"],
                    ), llm_router=router)
                # Keep only fields needed for the established audit-bound exporter.
                source = {key: response[key] for key in (
                    "query_id", "status", "nl_text", "candidates", "llm_calls", "llm_usage",
                )}
                if response["llm_audit"]["status"] != "persisted":
                    state["status"] = "audit_failed"
                    continue
                candidates = source["candidates"]
                selected = candidates[0]["id"] if candidates else None
                responses.append(source)
                bindings.append({"id": case["id"], "query_id": source["query_id"],
                                 "candidate_id": selected, "db_id": db_id})
                state["status"] = source["status"]
            except TimeoutError:
                state["status"] = "timeout"
            except asyncio.CancelledError:
                state["status"] = "cancelled"
                report["status"] = "cancelled"
                raise
            except Exception:
                state["status"] = "error"
            finally:
                active_call_limits.reset(token)
                checkpoint()
        store = UsageAuditStore(str(audit_path))
        predictions = export_predictions(dataset, responses, bindings,
            audit_lookup=store.read, require_candidate_audit=True) if bindings else []
        write_atomic(directory / "predictions.json", predictions)
        if manifest["profile"] == V2_PROFILE:
            from app.evaluation.contract_suite import evaluate_suite

            report["evaluation"] = evaluate_suite(dataset, predictions)
        else:
            report["evaluation"] = evaluate_sql(dataset["sql_cases"], predictions)
        report["status"] = "finished"
    finally:
        active_call_guard.reset(guard_token)
        ConnectionFactory.close(db_id)
        # Cancelled/unattempted cases retain the full declared denominator.
        known = {state["id"] for state in states}
        states.extend({"id": c["id"], "status": "unattempted"}
                      for c in manifest["cases"] if c["id"] not in known)
        if report["status"] == "running":
            report["status"] = "error"
        checkpoint()
    return report
