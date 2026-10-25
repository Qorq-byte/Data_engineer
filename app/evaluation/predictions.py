"""Explicit offline prediction bindings and reproducible, non-authenticating provenance."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from app.audit.candidates import audit_candidate_digests, candidate_digest
from app.audit.store import validated_usage

EXPORT_VERSION = "request-prediction-v1"
BOUND_EXPORT_VERSION = "request-prediction-v2"
_QUERY_ID = re.compile(r"q_[0-9a-f]{12}")
_MAX_ITEMS = 10000
_ROW_KEYS = {
    "id",
    "sql",
    "status",
    "finish_reason",
    "latency_ms",
    "cost_usd",
    "export_version",
    "provenance",
    "export_sha256",
}

_PROVENANCE_KEYS = {
    "dataset_sha256",
    "cases_sha256",
    "responses_sha256",
    "bindings_sha256",
    "query_id",
    "candidate_id",
    "db_id",
    "dialect",
    "question_match",
    "completion_source",
    "llm_calls",
    "llm_usage",
    "audit_match",
    "audit_sha256",
    "usage_scope",
    "sql_binding",
}
_HASH = re.compile(r"[0-9a-f]{64}")
_BOUND_PROVENANCE_KEYS = _PROVENANCE_KEYS | {
    "candidate_sha256", "generation_source", "candidate_finish_reason",
}


def fingerprint(value: object) -> str:
    try:
        data = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("Export input must be finite JSON") from None
    return hashlib.sha256(data).hexdigest()


def _text(value: object, *, empty: bool = False, limit: int = 256) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (not empty and not value.strip())
        or "\x00" in value
    ):
        raise ValueError("Invalid export identifier or text")
    return value


def _sql(value: object) -> str:
    text = _text(value, limit=1_048_576)
    if len(text.encode("utf-8")) > 1_048_576:
        raise ValueError("Export SQL exceeds byte limit")
    return text


def _query_id(value: object) -> str:
    result = _text(value)
    if not _QUERY_ID.fullmatch(result):
        raise ValueError("Invalid export query id")
    return result


def _objects(value: object, *, nonempty: bool = False) -> list[dict[str, Any]]:
    if (
        not isinstance(value, list)
        or len(value) > _MAX_ITEMS
        or (nonempty and not value)
        or any(not isinstance(x, dict) for x in value)
    ):
        raise ValueError("Export requires bounded object lists")
    return value


def _cases(dataset: object) -> list[dict[str, Any]]:
    if not isinstance(dataset, dict):
        raise ValueError("Export requires a dataset object")
    cases = _objects(dataset.get("sql_cases"), nonempty=True)
    ids = set()
    for case in cases:
        case_id = _text(case.get("id"), limit=128)
        _sql(case.get("gold_sql"))
        if case_id in ids or ("ordered" in case and type(case["ordered"]) is not bool):
            raise ValueError("Invalid or duplicate export cases")
        if "question" in case:
            _text(case["question"], limit=1_048_576)
        ids.add(case_id)
    return cases



def _calls(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("Invalid export budget count")
    return value


def _finish(candidate: dict[str, Any] | None) -> tuple[str | None, str]:
    if candidate is not None and "generation_source" in candidate:
        source = candidate["generation_source"]
        if source == "prepared":
            raise ValueError("Prepared SQL is not a model prediction")
        if source not in ("model_response", "model_stream", "stream_text", "unknown"):
            raise ValueError("Invalid candidate generation source")
    if candidate is None or not any(
        k in candidate for k in ("finish_reason", "refusal", "tool_calls", "function_call")
    ):
        return None, "unavailable"
    if any(candidate.get(k) for k in ("refusal", "tool_calls", "function_call")):
        return "other", "candidate_fields"
    reason = candidate.get("finish_reason")
    if reason in (None, "stop") and candidate.get("generation_source") in (
        "stream_text", "unknown",
    ):
        return None, "unavailable"
    return (reason if reason in (None, "stop", "length") else "other"), "candidate_fields"


def export_predictions(
    dataset: object,
    responses: object,
    bindings: object,
    *,
    audit_lookup: Callable[[str], dict[str, Any] | None] | None = None,
    require_candidate_audit: bool = False,
) -> list[dict[str, Any]]:
    if type(require_candidate_audit) is not bool or (
        require_candidate_audit and audit_lookup is None
    ):
        raise ValueError("Candidate audit requirement needs an audit lookup")
    cases = _cases(dataset)
    sources = _objects(responses)
    selections = _objects(bindings, nonempty=True)
    hashes = {
        "dataset_sha256": fingerprint(dataset),
        "cases_sha256": fingerprint(cases),
        "responses_sha256": fingerprint(responses),
        "bindings_sha256": fingerprint(bindings),
    }
    by_case = {case["id"]: case for case in cases}
    by_query: dict[str, dict[str, Any]] = {}
    for source in sources:
        query_id = _query_id(source.get("query_id"))
        if query_id in by_query:
            raise ValueError("Duplicate source query id")
        by_query[query_id] = source
    used_cases: set[str] = set()
    used_queries: set[str] = set()
    rows = []
    for binding in selections:
        if set(binding) - {"id", "query_id", "candidate_id", "db_id"}:
            raise ValueError("Unknown export binding fields")
        case_id, query_id = _text(binding.get("id"), limit=128), _query_id(binding.get("query_id"))
        db_id = _text(binding.get("db_id"), empty=True)
        if (
            case_id not in by_case
            or query_id not in by_query
            or case_id in used_cases
            or query_id in used_queries
        ):
            raise ValueError("Unknown or duplicate export binding")
        used_cases.add(case_id)
        used_queries.add(query_id)
        source, case = by_query[query_id], by_case[case_id]
        status = source.get("status")
        if status not in ("completed", "failed"):
            raise ValueError("Export requires a final workflow response")
        question_match = "not_available"
        if "question" in case:
            if source.get("nl_text") != case["question"]:
                raise ValueError("Export question binding mismatch")
            question_match = "exact_match"
        candidate_id = binding.get("candidate_id")
        candidate = None
        if candidate_id is not None:
            candidate_id = _text(candidate_id, limit=128)
            candidates = _objects(source.get("candidates"))
            selected = [
                c for c in candidates if c.get("id") == candidate_id and c.get("db_id") == db_id
            ]
            if len(selected) != 1 or selected[0].get("dialect") != "sqlite":
                raise ValueError("Candidate binding must select one SQLite candidate")
            candidate = selected[0]
        if status == "completed" and candidate is None:
            raise ValueError("Completed export requires an explicit candidate")
        sql = (
            _sql(candidate.get("sql_text"))
            if status == "completed" and candidate is not None
            else None
        )
        calls, observed = _calls(source.get("llm_calls")), validated_usage(source.get("llm_usage"))
        audit_match, audit_hash = "not_requested", None
        bound_digest = None
        if audit_lookup is not None:
            record = audit_lookup(query_id)
            if (
                not isinstance(record, dict)
                or type(record.get("schema_version")) is not int
                or record["schema_version"] not in (1, 2)
            ):
                raise ValueError("Requested audit record is unavailable")
            if (
                record.get("query_id") != query_id
                or record.get("outcome") != status
                or calls is None
                or observed is None
            ):
                raise ValueError("Audit metadata mismatch")
            audit_usage = validated_usage(record.get("usage"))
            if _calls(record.get("llm_calls")) != calls or fingerprint(audit_usage) != fingerprint(
                observed
            ):
                raise ValueError("Audit metadata mismatch")
            audit_match, audit_hash = "metadata_match", fingerprint(record)
            digests = audit_candidate_digests(record) if record["schema_version"] == 2 else None
            if status == "completed" and candidate is not None:
                if digests is not None:
                    bound_digest = candidate_digest(query_id, candidate)
                    if bound_digest not in digests:
                        raise ValueError("Audit candidate binding mismatch")
                    audit_match = "candidate_match"
                elif require_candidate_audit:
                    raise ValueError("Candidate audit binding requires a v2 snapshot")
        finish_candidate = candidate
        if bound_digest is not None and candidate is not None:
            finish_candidate = {
                **candidate,
                "generation_source": candidate.get("generation_source", "unknown"),
                "finish_reason": candidate.get("finish_reason"),
            }
        reason, completion_source = _finish(finish_candidate)
        provenance = {
            **hashes,
            "query_id": query_id,
            "candidate_id": candidate_id,
            "db_id": db_id,
            "dialect": candidate.get("dialect") if candidate is not None else None,
            "question_match": question_match,
            "completion_source": completion_source,
            "llm_calls": calls,
            "llm_usage": observed,
            "audit_match": audit_match,
            "audit_sha256": audit_hash,
            "usage_scope": "entire_query_including_retries_candidates_and_targets",
            "sql_binding": (
                "candidate_digest_matched" if bound_digest is not None
                else "caller_supplied_not_verified_by_audit"
            ),
        }
        if bound_digest is not None and candidate is not None:
            provenance.update(
                candidate_sha256=bound_digest,
                generation_source=candidate.get("generation_source", "unknown"),
                candidate_finish_reason=candidate.get("finish_reason"),
            )
        row = {
            "id": case_id,
            "sql": sql,
            "status": status,
            "finish_reason": reason,
            "latency_ms": None,
            "cost_usd": None,
            "export_version": BOUND_EXPORT_VERSION if bound_digest is not None else EXPORT_VERSION,
            "provenance": provenance,
        }
        row["export_sha256"] = fingerprint(row)
        rows.append(row)
    validate_exported_predictions(cases, rows, dataset=dataset)
    return rows


def validate_exported_predictions(
    cases: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    dataset: object = None,
) -> None:
    """Verify new exported rows, leaving legacy prediction rows unchanged.

    Digests detect accidental edits and dataset mismatches; they are not signatures.
    """
    if not isinstance(predictions, list):
        raise ValueError("Predictions must be a list")
    case_hash = fingerprint(cases)
    seen_queries: set[str] = set()
    for row in predictions:
        if not isinstance(row, dict):
            raise ValueError("Predictions must contain objects")
        if "export_version" not in row:
            if "export_sha256" in row:
                raise ValueError("Prediction export version is missing")
            continue
        version = row.get("export_version")
        if version not in (EXPORT_VERSION, BOUND_EXPORT_VERSION) or set(row) != _ROW_KEYS:
            raise ValueError("Unsupported prediction export shape or version")
        provenance = row.get("provenance")
        expected_keys = (
            _BOUND_PROVENANCE_KEYS if version == BOUND_EXPORT_VERSION else _PROVENANCE_KEYS
        )
        if not isinstance(provenance, dict) or set(provenance) != expected_keys:
            raise ValueError("Invalid prediction export provenance")
        for key in ("dataset_sha256", "cases_sha256", "responses_sha256", "bindings_sha256"):
            if not isinstance(provenance[key], str) or not _HASH.fullmatch(provenance[key]):
                raise ValueError("Invalid prediction export source hash")
        if provenance.get("cases_sha256") != case_hash:
            raise ValueError("Prediction export cases mismatch")
        if dataset is not None and provenance.get("dataset_sha256") != fingerprint(dataset):
            raise ValueError("Prediction export dataset mismatch")
        if row.get("export_sha256") != fingerprint(
            {k: v for k, v in row.items() if k != "export_sha256"}
        ):
            raise ValueError("Prediction export hash mismatch")
        query_id = _query_id(provenance.get("query_id"))
        if query_id in seen_queries:
            raise ValueError("Duplicate query in prediction exports")
        seen_queries.add(query_id)
        if row.get("status") not in ("completed", "failed") or row.get("finish_reason") not in (
            None,
            "stop",
            "length",
            "other",
        ):
            raise ValueError("Invalid exported prediction state")
        if row.get("latency_ms") is not None or row.get("cost_usd") is not None:
            raise ValueError("Unsupported exported telemetry")
        if row["status"] == "completed":
            _sql(row.get("sql"))
            _text(provenance.get("candidate_id"), limit=128)
            if provenance.get("dialect") != "sqlite":
                raise ValueError("Unsupported exported dialect")
        elif row.get("sql") is not None:
            raise ValueError("Failed export cannot contain SQL")
        _text(provenance.get("db_id"), empty=True)
        observed = validated_usage(provenance.get("llm_usage"))
        calls = _calls(provenance.get("llm_calls"))
        if (
            provenance["usage_scope"] != "entire_query_including_retries_candidates_and_targets"
            or provenance["sql_binding"] != (
                "candidate_digest_matched" if version == BOUND_EXPORT_VERSION
                else "caller_supplied_not_verified_by_audit"
            )
        ):
            raise ValueError("Unsupported prediction export scope")
        if provenance["question_match"] not in ("not_available", "exact_match") or provenance[
            "completion_source"
        ] not in ("unavailable", "candidate_fields"):
            raise ValueError("Invalid prediction export completion provenance")
        if provenance["completion_source"] == "unavailable" and row["finish_reason"] is not None:
            raise ValueError("Unknown candidate completion cannot imply a finish reason")
        if version == BOUND_EXPORT_VERSION:
            if row["status"] != "completed" or provenance["audit_match"] != "candidate_match":
                raise ValueError("Candidate-bound export requires a completed audited prediction")
            candidate = {
                "id": provenance["candidate_id"], "db_id": provenance["db_id"],
                "dialect": provenance["dialect"], "sql_text": row["sql"],
                "generation_source": provenance["generation_source"],
                "finish_reason": provenance["candidate_finish_reason"],
            }
            if provenance["candidate_sha256"] != candidate_digest(query_id, candidate):
                raise ValueError("Exported candidate binding mismatch")
            if _finish(candidate) != (row["finish_reason"], provenance["completion_source"]):
                raise ValueError("Exported candidate completion mismatch")
        if provenance["audit_match"] == "not_requested":
            if provenance["audit_sha256"] is not None:
                raise ValueError("Unrequested audit cannot have verification metadata")
        elif provenance["audit_match"] in ("metadata_match", "candidate_match"):
            if provenance["audit_match"] == "candidate_match" and version != BOUND_EXPORT_VERSION:
                raise ValueError("Candidate match requires a v2 prediction export")
            digest = provenance["audit_sha256"]
            if (
                not isinstance(digest, str)
                or not _HASH.fullmatch(digest)
                or calls is None
                or observed is None
            ):
                raise ValueError("Invalid audit match provenance")
        else:
            raise ValueError("Unsupported audit match provenance")
