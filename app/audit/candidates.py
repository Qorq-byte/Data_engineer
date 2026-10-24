"""Bounded candidate digests, never SQL persistence or authenticity guarantees."""

import hashlib
import json
import re
from typing import Any

CANDIDATE_DIGEST_VERSION = "candidate-binding-v1"
_HASH = re.compile(r"[0-9a-f]{64}")
_QUERY_ID = re.compile(r"q_[0-9a-f]{12}")
_SOURCES = ("model_response", "model_stream", "prepared", "stream_text", "unknown")


def _text(value: object, limit: int, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or "\x00" in value
        or (not empty and not value.strip())
        or len(value.encode("utf-8")) > limit
    ):
        raise ValueError("Invalid candidate binding text")
    return value


def candidate_digest(query_id: str, candidate: dict[str, Any]) -> str:
    """Bind exact SQL and its candidate-local evidence, without rewriting either."""
    if not _QUERY_ID.fullmatch(query_id):
        raise ValueError("Invalid candidate binding query")
    source = candidate.get("generation_source", "unknown")
    reason = candidate.get("finish_reason")
    if (
        source not in _SOURCES
        or reason not in (None, "stop")
        or any(candidate.get(key) for key in ("refusal", "tool_calls", "function_call"))
    ):
        raise ValueError("Invalid candidate binding evidence")
    payload = {
        "binding_version": CANDIDATE_DIGEST_VERSION,
        "query_id": query_id,
        "id": _text(candidate.get("id"), 128),
        "db_id": _text(candidate.get("db_id"), 256, empty=True),
        "dialect": _text(candidate.get("dialect"), 128),
        "sql_text": _text(candidate.get("sql_text"), 1_048_576),
        "generation_source": source,
        "finish_reason": reason,
    }
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def checked_digests(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > 1000
        or any(not isinstance(item, str) or not _HASH.fullmatch(item) for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("Invalid audit candidate digests")
    return sorted(value)


def candidate_digests(query_id: str, candidates: object) -> list[str]:
    if not isinstance(candidates, list) or len(candidates) > 1000:
        raise ValueError("Invalid audit candidate collection")
    digests = []
    identities: set[tuple[str, str]] = set()
    total_bytes = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("Invalid audit candidate")
        digest = candidate_digest(query_id, candidate)
        identity = (candidate["id"], candidate["db_id"])
        if identity in identities:
            raise ValueError("Duplicate audit candidate identity")
        identities.add(identity)
        total_bytes += len(candidate["sql_text"].encode("utf-8"))
        if total_bytes > 16_777_216:
            raise ValueError("Audit candidate SQL exceeds total byte limit")
        digests.append(digest)
    return checked_digests(digests)


def audit_candidate_digests(record: dict[str, Any]) -> list[str]:
    if record.get("candidate_digest_version") != CANDIDATE_DIGEST_VERSION:
        raise ValueError("Unsupported audit candidate digest version")
    return checked_digests(record.get("candidate_digests"))
