"""Offline flat-token estimates from explicit operator prices and observed audit usage."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from app.audit.candidates import audit_candidate_digests
from app.audit.store import validated_usage
from app.evaluation.predictions import fingerprint

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")
_QUERY_ID = re.compile(r"q_[0-9a-f]{12}")
_RATE = re.compile(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,12})?")
_UNCERTAIN = {"unknown", "redacted", "adapter", "mock"}
_MAX_COUNT = 2**63 - 1


@dataclass(frozen=True)
class _PriceBook:
    label: str
    as_of: str
    sha256: str
    rates: dict[tuple[str, str], tuple[Decimal | None, Decimal | None]]


def _identity(value: object) -> str:
    if (
        not isinstance(value, str)
        or not _IDENTITY.fullmatch(value)
        or "://" in value
        or value.lower() in _UNCERTAIN
    ):
        raise ValueError("Invalid flat-price identity")
    return value


def _rate(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _RATE.fullmatch(value):
        raise ValueError("Flat price must be a bounded nonnegative decimal string or null")
    return Decimal(value)


def _prices(value: object) -> _PriceBook:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "currency", "label", "as_of", "prices"}
        or value["version"] != "flat-token-prices-v1"
        or value["currency"] != "USD"
    ):
        raise ValueError("Unsupported flat price book")
    label, as_of, raw = _identity(value["label"]), value["as_of"], value["prices"]
    if not isinstance(as_of, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", as_of):
        raise ValueError("Invalid price book date")
    date.fromisoformat(as_of)
    if not isinstance(raw, list) or len(raw) > 1000:
        raise ValueError("Invalid flat price entries")
    rates = {}
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {
            "provider",
            "model",
            "prompt_usd_per_million",
            "completion_usd_per_million",
        }:
            raise ValueError("Invalid flat price entry")
        key = (_identity(entry["provider"]), _identity(entry["model"]))
        if key in rates:
            raise ValueError("Duplicate flat price entry")
        rates[key] = (
            _rate(entry["prompt_usd_per_million"]),
            _rate(entry["completion_usd_per_million"]),
        )
    return _PriceBook(label, as_of, fingerprint(value), rates)


def _money(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_COUNT:
        raise ValueError("Invalid audit count")
    return value


def _attempt(item: dict[str, Any], book: _PriceBook) -> dict[str, Any]:
    reasons = []
    provider, model = item["provider"], item["model"]
    prompt, completion = item["prompt_tokens"], item["completion_tokens"]
    rates = book.rates.get((provider, model), (None, None))
    if item["status"] == "pending":
        reasons.append("attempt_pending")
    if provider.lower() in _UNCERTAIN or model.lower() in _UNCERTAIN:
        reasons.append("unverified_identity")
    if item["source"] != "transport_boundary":
        reasons.append("unverified_source")
    if item["usage_basis"] not in ("response_fields", "sdk_normalized"):
        reasons.append("unverified_usage_basis")
    if prompt is None or completion is None:
        reasons.append("usage_missing")
    elif prompt > _MAX_COUNT or completion > _MAX_COUNT:
        reasons.append("token_count_out_of_range")
    if rates[0] is None or rates[1] is None:
        reasons.append("price_missing")
    estimate = None
    if not reasons:
        assert rates[0] is not None and rates[1] is not None
        estimate = _money((Decimal(prompt) * rates[0] + Decimal(completion) * rates[1]) / 1000000)
    return {
        **{
            key: item[key]
            for key in (
                "attempt",
                "provider",
                "model",
                "status",
                "source",
                "usage_basis",
                "prompt_tokens",
                "completion_tokens",
            )
        },
        "prompt_usd_per_million": _money(rates[0]) if rates[0] is not None else None,
        "completion_usd_per_million": _money(rates[1]) if rates[1] is not None else None,
        "estimated_usd": estimate,
        "unavailable_reasons": reasons,
    }


def _query(record: dict[str, Any], book: _PriceBook) -> dict[str, Any]:
    if (
        type(record.get("schema_version")) is not int
        or record["schema_version"] not in (1, 2)
        or record.get("outcome") not in ("completed", "failed", "cancelled", "error")
    ):
        raise ValueError("Unsupported usage audit record")
    if record["schema_version"] == 2:
        audit_candidate_digests(record)
    query_id = record.get("query_id")
    if not isinstance(query_id, str) or not _QUERY_ID.fullmatch(query_id):
        raise ValueError("Invalid audit query id")
    calls = _count(record.get("llm_calls"))
    usage = validated_usage(record.get("usage"))
    attempts = [_attempt(item, book) for item in usage["attempts"]] if usage is not None else []
    amounts = [
        Decimal(item["estimated_usd"]) for item in attempts if item["estimated_usd"] is not None
    ]
    reasons = []
    dropped = _count(usage["dropped_attempts"]) if usage is not None else None
    if usage is None:
        reasons.append("usage_unavailable")
    elif not attempts:
        reasons.append("no_attempts")
    if len(amounts) != len(attempts):
        reasons.append("unpriced_attempts")
    if dropped:
        reasons.append("dropped_attempts")
    if usage is not None and calls != len(attempts) + (dropped or 0):
        reasons.append("attempt_accounting_mismatch")
    subtotal = _money(sum(amounts, Decimal(0))) if amounts else None
    denominator = len(attempts) + dropped if dropped is not None else 0
    return {
        "query_id": query_id,
        "outcome": record["outcome"],
        "audit_schema_version": record["schema_version"],
        "audit_sha256": fingerprint(record),
        "llm_calls": calls,
        "attempt_count": len(attempts) if usage is not None else None,
        "dropped_attempts": dropped,
        "priced_attempt_count": len(amounts),
        "priced_attempt_coverage": len(amounts) / denominator if denominator else None,
        "known_subtotal_usd": subtotal,
        "estimated_total_usd": subtotal if not reasons else None,
        "unavailable_reasons": reasons,
        "attempts": attempts,
    }


def estimate_usage_costs(records: object, price_book: object) -> dict[str, Any]:
    """Estimate unique audit requests with explicit flat prices; never attribute candidate costs."""
    if not isinstance(records, list) or not 1 <= len(records) <= 100:
        raise ValueError("Usage estimates require 1 to 100 unique audit records")
    book = _prices(price_book)
    with localcontext() as context:
        context.prec = 64
        queries = []
        seen = set()
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Invalid usage audit record")
            row = _query(record, book)
            if row["query_id"] in seen:
                raise ValueError("Duplicate audit query in estimate")
            seen.add(row["query_id"])
            queries.append(row)
        partials = [
            Decimal(row["known_subtotal_usd"])
            for row in queries
            if row["known_subtotal_usd"] is not None
        ]
        known = _money(sum(partials, Decimal(0))) if partials else None
        complete = all(row["estimated_total_usd"] is not None for row in queries)
        observed = (
            sum(row["attempt_count"] for row in queries)
            if all(row["attempt_count"] is not None for row in queries)
            else None
        )
        dropped = (
            sum(row["dropped_attempts"] for row in queries)
            if all(row["dropped_attempts"] is not None for row in queries)
            else None
        )
        priced = sum(row["priced_attempt_count"] for row in queries)
        denominator = observed + dropped if observed is not None and dropped is not None else 0
        result = {
            "report_version": "usage-cost-estimate-v1",
            "currency": "USD",
            "estimate_scope": "flat_prompt_completion_tokens_entire_query",
            "billing_verified": False,
            "price_book": {"label": book.label, "as_of": book.as_of, "sha256": book.sha256},
            "queries": queries,
            "summary": {
                "query_count": len(queries),
                "attempt_count": observed,
                "dropped_attempts": dropped,
                "priced_attempt_count": priced,
                "priced_attempt_coverage": priced / denominator if denominator else None,
                "known_subtotal_usd": known,
                "estimated_total_usd": known if complete else None,
            },
        }
        result["report_sha256"] = fingerprint(result)
        return result
