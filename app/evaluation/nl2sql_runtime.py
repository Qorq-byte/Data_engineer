"""One isolated native Harness batch, with persistent pre-transport reservations."""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.evaluation.artifacts import read_json
from app.evaluation.embedding_runtime import write_atomic
from app.evaluation.nl2sql_batch import (
    BASE_URL,
    LIMITS,
    MODEL,
    PROMPTS,
    load_suite,
    manifest_digest,
)
from app.evaluation.predictions import export_predictions, fingerprint
from app.harness.constraint import ConstraintEnforcer, ConstraintLimits
from app.llm.budget import active_call_guard, active_call_limits
from app.llm.router import LiteLLMRouter, ProviderConfig, RouterConfig


def error_code(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, asyncio.CancelledError)):
        return "CANCELLED" if isinstance(exc, asyncio.CancelledError) else "TIMEOUT"
    name = type(exc).__name__
    return {"IncompleteCompletionError": "INCOMPLETE_COMPLETION",
            "ProviderTimeoutError": "TIMEOUT", "ProviderConnectionError": "CONNECTION_FAILED",
            "RateLimitError": "RATE_LIMITED", "ProviderServerError": "PROVIDER_SERVER_ERROR",
            "AuthenticationError": "AUTHENTICATION_FAILED",
            "BadRequestError": "BAD_REQUEST",
            "ProviderAuthenticationError": "AUTHENTICATION_FAILED"}.get(name, "PROVIDER_ERROR")


class CallJournal:
    def __init__(self, directory: Path):
        self.path = directory / "requests.json"
        self.prompts = directory / "prompts.json"
        self.records: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.case_id: str | None = None
        self.allowed = {c["id"] for c in load_suite()["sql_cases"]}
        self.io_failed = False
        self.policy_failure: str | None = None
        inventory = read_json(PROMPTS)
        if not isinstance(inventory, list):
            raise ValueError("Invalid prompt inventory")
        self.inventory = {r["id"]: r for r in inventory}
        write_atomic(self.path, self.records)

    def reserve(self, messages: list[dict[str, str]]) -> None:
        if (self.io_failed or self.case_id not in self.allowed
                or len(self.records) >= LIMITS["max_calls"]
                or any(row["id"] == self.case_id for row in self.records)):
            raise RuntimeError("CALL_BUDGET_OR_DUPLICATE_REJECTED")
        size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
        if size > LIMITS["message_bytes"]:
            raise RuntimeError("MESSAGE_BUDGET_REJECTED")
        self.messages.append({"id": self.case_id, "messages": messages})
        self.publish(self.prompts, self.messages)  # Private; no gold or fixture rows.
        self.records.append({"id": self.case_id, "state": "reserved", "message_bytes": size,
                             "messages_sha256": fingerprint(messages), "error_code": None})
        self.publish(self.path, self.records)  # Must succeed before native SDK transport.

    def update(self, **values: Any) -> None:
        self.records[-1].update(values)
        self.publish(self.path, self.records)

    def publish(self, path: Path, value: object) -> None:
        try:
            write_atomic(path, value)
        except (OSError, ValueError):
            self.io_failed = True
            raise


def approved_guard(provider: ProviderConfig, journal: CallJournal) -> Callable[..., None]:
    def guard(actual: object, model: str, messages: list[dict[str, str]], n: int,
              output_tokens: int | None, stream: bool, overrides: dict[str, Any]) -> None:
        if (actual is not provider or model != MODEL or type(n) is not int or n != 1
                or stream is not False
                or output_tokens != LIMITS["max_output_tokens"]
                or overrides not in ({}, {"max_tokens": LIMITS["max_output_tokens"]})):
            journal.policy_failure = "CALL_POLICY_REJECTED"
            raise RuntimeError("CALL_POLICY_REJECTED")
        expected = journal.inventory.get(journal.case_id)
        if expected is None or fingerprint(messages) != expected["messages_sha256"]:
            journal.policy_failure = "PROMPT_IDENTITY_MISMATCH"
            journal.publish(journal.path.with_name(f"rejected-prompt-{journal.case_id}.json"),
                            {"messages": messages, "expected": expected,
                             "actual_sha256": fingerprint(messages)})
            raise RuntimeError("PROMPT_IDENTITY_MISMATCH")
        journal.reserve(messages)
    return guard


class ControlledRouter(LiteLLMRouter):
    def __init__(self, provider: ProviderConfig, journal: CallJournal):
        super().__init__(RouterConfig(providers={"deepseek": provider}, default_model=MODEL,
            fallback_chain=[], max_retries=0, timeout_seconds=LIMITS["request_seconds"],
            max_concurrency=1, max_output_tokens=LIMITS["max_output_tokens"]), mock_mode=False)
        self.journal = journal

    async def _call_provider(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            value = await super()._call_provider(*args, **kwargs)
        except BaseException as exc:
            code = error_code(exc)
            self.journal.update(state="response_rejected" if code == "INCOMPLETE_COMPLETION"
                                else "unknown_transport", error_code=code,
                                elapsed_ms=(time.perf_counter() - started) * 1000)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RuntimeError(code) from None
        self.journal.update(state="response_received",
                            elapsed_ms=(time.perf_counter() - started) * 1000)
        return value


def latency(samples: list[float]) -> dict[str, Any]:
    values = sorted(samples)
    return {"sample_count": len(values), **{f"p{p}_ms": values[math.ceil(p / 100 * len(values))-1]
            if values else None for p in (50, 95, 99)},
            "policy": "Nearest-rank over attempted cases including failed/cancelled cases"}


def grouped_scores(suite: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    def group(cases: list[dict[str, Any]]) -> dict[str, Any]:
        ids = {case["id"] for case in cases}
        rows = [row for row in evaluation["details"] if row["id"] in ids]
        correct = sum(all(row["contract_match"] for row in rows if row["id"] == identity)
                      for identity in ids)
        return {"case_count": len(ids), "check_count": len(rows), "robust_match_count": correct,
                "robust_match_rate": correct / len(ids) if ids else None,
                "error_counts": dict(Counter(row["error_code"] for row in rows
                                              if row["error_code"]))}
    cases = suite["sql_cases"]
    return {"by_split": {split: group([c for c in cases if c["split"] == split])
                         for split in ("dev", "holdout")},
            "by_tag": {tag: group([c for c in cases if tag in c["tags"]])
                       for tag in sorted({tag for c in cases for tag in c["tags"]})}}


async def run_batch(manifest: dict[str, Any], api_key: str, directory: Path) -> dict[str, Any]:
    from app.api.query import QueryRequest
    from app.audit.store import UsageAuditStore
    from app.config.settings import settings
    from app.db.connections import ConnectionFactory, DatabaseType
    from app.evaluation.acceptance_data import DIRECTORY
    from app.evaluation.acceptance_sql import evaluate_sql
    from app.knowledge.schema_retriever import SchemaRetriever
    from app.workflow.query_service import run_query

    if ConnectionFactory.list_all() or settings.llm_audit_path != str(directory / "usage.sqlite3"):
        raise ValueError("Worker database/audit isolation missing")
    if not settings.llm_audit_candidate_digests:
        raise ValueError("Candidate audit binding required")
    suite = load_suite()
    journal = CallJournal(directory)
    provider = ProviderConfig("deepseek", "DEEPSEEK_API_KEY", [MODEL], BASE_URL, api_key=api_key)
    router = ControlledRouter(provider, journal)
    batch = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=30))
    guard = active_call_guard.set(approved_guard(provider, journal))
    states = [{"id": c["id"], "status": "unattempted", "elapsed_ms": None,
               "generated": False, "validation_passed": False, "error_code": None}
              for c in manifest["cases"]]
    responses: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    report: dict[str, Any] = {"profile": manifest["profile"], "model": MODEL,
        "manifest_sha256": manifest_digest(manifest), "status": "running", "cases": states,
        "physical_calls_reserved": 0, "actual_cost_usd": None, "evaluation": None,
        "scope": manifest["scope"], "retrieval": manifest["retrieval"]}

    def checkpoint() -> None:
        if journal.io_failed:
            raise RuntimeError("JOURNAL_PUBLICATION_FAILED")
        if journal.policy_failure:
            raise RuntimeError(journal.policy_failure)
        report["physical_calls_reserved"] = len(journal.records)
        report["native_budget_calls"] = batch.llm_calls_used
        report["unknown_transport_outcomes"] = sum(row["state"] in
            ("reserved", "unknown_transport") for row in journal.records)
        write_atomic(directory / "responses.json", responses)
        write_atomic(directory / "bindings.json", bindings)
        write_atomic(directory / "report.json", report)
        if sum(p.stat().st_size for p in directory.rglob("*") if p.is_file()) > LIMITS[
                "max_artifact_bytes"]:
            raise RuntimeError("ARTIFACT_BUDGET_EXCEEDED")

    async def observe_event(event: str, payload: dict[str, Any]) -> None:
        # Save protocol/Harness evidence privately; no global registry/RAG mutation.
        events.append({"event": event, "data": payload})

    events: list[dict[str, Any]] = []
    db_id = "nl2sql-v3-fixture"
    try:
        conn = await ConnectionFactory.create(DatabaseType.SQLITE,
            {"path": "fixture.sqlite3"}, db_id)
        for name in ("schema.sql", "base.sql"):
            conn.executescript((DIRECTORY / name).read_text(encoding="utf-8"))
        conn.execute("PRAGMA query_only=ON")
        for case, state in zip(manifest["cases"], states, strict=True):
            own = ConstraintEnforcer(ConstraintLimits(max_llm_calls_per_query=1))
            token = active_call_limits.set((batch, own))
            journal.case_id = case["id"]
            events = []
            started = time.perf_counter()
            try:
                state["status"] = "running"
                checkpoint()
                async with asyncio.timeout(LIMITS["case_seconds"]):
                    response = await run_query(QueryRequest(nl_text=case["question"],
                        session_id=f"eval-{uuid.uuid4().hex}", domain="", database=db_id,
                        dialect="sqlite", num_candidates=1, execute=False, run_validation=True,
                        model=MODEL), observe_event, llm_router=router,
                        schema_retriever=SchemaRetriever())
                state["status"] = response["status"]
                state["usage"] = response["llm_usage"]
                state["generated"] = any(c.get("generation_source") == "model_response"
                    and c.get("finish_reason") == "stop" for c in response["candidates"])
                validation = response.get("validation") or {}
                state["validation_passed"] = validation.get("passed") is True
                state["workflow_ids"] = response["workflow_ids"]
                state["node_order"] = [e["data"]["stage"] for e in events
                    if e["event"] == "stage" and e["data"].get("status") == "started"]
                state["retrieval_method"] = "keyword-only retriever or node fallback"
                state["schema_warning_codes"] = [w.split(":", 1)[0] for w in
                    response.get("warnings", []) if w.startswith("SCHEMA_")]
                if response["llm_audit"]["status"] != "persisted":
                    state.update(status="audit_failed", error_code="AUDIT_FAILED")
                    continue
                source = {key: response[key] for key in ("query_id", "status", "nl_text",
                          "candidates", "llm_calls", "llm_usage")}
                responses.append(source)
                candidate = source["candidates"][0]["id"] if source["candidates"] else None
                bindings.append({"id": case["id"], "query_id": source["query_id"],
                                 "candidate_id": candidate, "db_id": db_id})
                inferred = None
                finishes = {a.get("finish_status") for a in
                            response["llm_usage"].get("attempts", [])}
                if "length" in finishes:
                    inferred = "TRUNCATED_COMPLETION"
                elif "other" in finishes:
                    inferred = "REFUSAL_OR_TOOL_COMPLETION"
                elif not state["generated"]:
                    inferred = "NO_SQL_CANDIDATE"
                elif not state["validation_passed"]:
                    inferred = ("VALIDATION_SYNTAX_FAILED" if validation.get("syntax_errors")
                                else "VALIDATION_FAILED")
                state["error_code"] = next((row["error_code"] for row in journal.records
                    if row["id"] == case["id"] and row["error_code"]), inferred)
            except asyncio.CancelledError:
                state.update(status="cancelled", error_code="CANCELLED")
                report["status"] = "cancelled"
                raise
            except TimeoutError:
                state.update(status="timeout", error_code="TIMEOUT")
            except Exception:
                state.update(status="error", error_code="CASE_FAILED")
            finally:
                state["elapsed_ms"] = (time.perf_counter() - started) * 1000
                active_call_limits.reset(token)
                write_atomic(directory / f"events-{case['id']}.json", events)
                checkpoint()
        report["status"] = "finished"
    finally:
        active_call_guard.reset(guard)
        ConnectionFactory.close(db_id)
        store = UsageAuditStore(str(directory / "usage.sqlite3"))
        predictions = export_predictions(suite, responses, bindings,
            audit_lookup=store.read, require_candidate_audit=True) if bindings else []
        write_atomic(directory / "predictions.json", predictions)
        evaluation = evaluate_sql(suite, predictions)
        report.update(evaluation=evaluation, **grouped_scores(suite, evaluation))
        report["latency"] = latency([s["elapsed_ms"] for s in states
                                     if s["elapsed_ms"] is not None])
        report["generated_count"] = sum(s["generated"] for s in states)
        report["validation_passed_count"] = sum(s["validation_passed"] for s in states)
        report["case_error_counts"] = dict(Counter(s["error_code"] for s in states
                                                   if s["error_code"]))
        report["usage"] = {"observed_attempts": sum(s.get("usage", {}).get("attempt_count", 0)
                            for s in states), "known_prompt_tokens": sum(
            s.get("usage", {}).get("known_prompt_tokens", 0) for s in states),
            "known_completion_tokens": sum(s.get("usage", {}).get("known_completion_tokens", 0)
                                            for s in states),
            "scope": "SDK/request observations only; unknown calls not counted as zero usage"}
        covered = sum(s.get("usage", {}).get("attempt_count", 0)
                      - s.get("usage", {}).get("missing_usage_count", 0) for s in states)
        reserved = len(journal.records)
        report["usage"].update(covered_attempts=covered,
            unknown_usage_attempts=max(0, reserved - covered),
            usage_coverage=covered / reserved if reserved else None,
            prompt_tokens=report["usage"]["known_prompt_tokens"]
                if reserved and covered == reserved else None,
            completion_tokens=report["usage"]["known_completion_tokens"]
                if reserved and covered == reserved else None)
        if report["status"] == "running":
            report["status"] = "error"
        checkpoint()
    return report
