"""Opt-in local SQLite usage snapshots; no SQL text, prompts or raw exceptions."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.audit.candidates import (
    CANDIDATE_DIGEST_VERSION,
    audit_candidate_digests,
    checked_digests,
)

_QUERY_ID = re.compile(r"q_[0-9a-f]{12}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")
_OUTCOMES = {"completed", "failed", "cancelled", "error"}
_STATUSES = {"pending", "completed", "incomplete", "failed", "timeout", "cancelled", "closed"}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class UsageAuditRecord:
    query_id: str
    session_id: str
    outcome: str
    llm_calls: int
    usage: dict[str, Any]
    started_at: str = field(default_factory=_utc_now)
    finished_at: str = field(default_factory=_utc_now)
    candidate_digests: list[str] | None = None


def _name(value: object) -> str:
    return (
        value
        if isinstance(value, str) and _NAME.fullmatch(value) and "://" not in value
        else "redacted"
    )


def _token(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def usage_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    """Project bounded metadata fields and recompute partial totals without writing storage."""
    raw = value.get("attempts", [])
    if not isinstance(raw, list) or len(raw) > 1000:
        raise ValueError("Invalid audit attempts")
    attempts = []
    for ordinal, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError("Invalid audit attempt")
        prompt, completion = (
            _token(item.get("prompt_tokens")),
            _token(item.get("completion_tokens")),
        )
        missing = None
        if prompt is None or completion is None:
            missing = item.get("missing_reason")
            if missing not in ("usage_missing", "usage_invalid_or_partial"):
                missing = "usage_invalid_or_partial"
        latency = item.get("latency_ms")
        if (
            not isinstance(latency, (int, float))
            or isinstance(latency, bool)
            or not math.isfinite(latency)
            or latency < 0
        ):
            latency = None
        attempts.append(
            {
                "attempt": ordinal,
                "provider": _name(item.get("provider")),
                "model": _name(item.get("model")),
                "source": item.get("source")
                if item.get("source") in ("transport_boundary", "adapter_boundary")
                else "unknown",
                "status": item.get("status") if item.get("status") in _STATUSES else "failed",
                "latency_ms": latency,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "missing_reason": missing,
                "finish_status": item.get("finish_status")
                if item.get("finish_status") in ("unknown", "stop", "length", "other")
                else "other",
                "usage_basis": item.get("usage_basis")
                if item.get("usage_basis") in ("response_fields", "sdk_normalized")
                else "unknown",
            }
        )
    missing_count = sum(a["missing_reason"] is not None for a in attempts)
    dropped = _token(value.get("dropped_attempts"))
    if dropped is None:
        raise ValueError("Invalid audit dropped count")
    return {
        "attempt_count": len(attempts),
        "attempts": attempts,
        "known_prompt_tokens": sum(
            a["prompt_tokens"] for a in attempts if a["prompt_tokens"] is not None
        ),
        "known_completion_tokens": sum(
            a["completion_tokens"] for a in attempts if a["completion_tokens"] is not None
        ),
        "missing_usage_count": missing_count,
        "usage_coverage": (len(attempts) - missing_count) / len(attempts) if attempts else None,
        "complete": not missing_count
        and not dropped
        and all(a["status"] != "pending" for a in attempts),
        "dropped_attempts": dropped,
    }


def _encode(record: UsageAuditRecord) -> str:
    if not _QUERY_ID.fullmatch(record.query_id) or record.outcome not in _OUTCOMES:
        raise ValueError("Invalid audit identity or outcome")
    if _token(record.llm_calls) is None:
        raise ValueError("Invalid audit budget count")
    candidate_fields = (
        {"candidate_digest_version": CANDIDATE_DIGEST_VERSION,
         "candidate_digests": checked_digests(record.candidate_digests)}
        if record.candidate_digests is not None else {}
    )
    payload = json.dumps(
        {
            "schema_version": 2 if record.candidate_digests is not None else 1,
            **candidate_fields,
            "query_id": record.query_id,
            "session_ref": hashlib.sha256(record.session_id.encode("utf-8")).hexdigest(),
            "outcome": record.outcome,
            "llm_calls": record.llm_calls,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "usage": usage_snapshot(record.usage),
        },
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    if len(payload.encode("utf-8")) > 1_048_576:
        raise ValueError("Audit record exceeds size limit")
    return payload


def _schema(conn: sqlite3.Connection, *, create: bool) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }
    if version == 0 and not tables and create:
        conn.execute(
            "CREATE TABLE llm_usage_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "query_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)"
        )
        conn.execute("PRAGMA user_version=1")
        return
    if version != 1 or tables != {"llm_usage_audit"}:
        raise ValueError("Unsupported audit database schema")
    columns = [r[1] for r in conn.execute("PRAGMA table_info(llm_usage_audit)")]
    if columns != ["id", "query_id", "payload"]:
        raise ValueError("Unsupported audit table schema")


class UsageAuditStore:
    def __init__(self, path: str, max_records: int = 1000) -> None:
        if not path.strip() or type(max_records) is not int or not 1 <= max_records <= 10000:
            raise ValueError("Invalid audit configuration")
        self.path = Path(path)
        self.max_records = max_records

    def write(self, record: UsageAuditRecord) -> None:
        payload = _encode(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(self.path, timeout=0.2)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            _schema(conn, create=True)
            existing = conn.execute(
                "SELECT payload FROM llm_usage_audit WHERE query_id=?", (record.query_id,)
            ).fetchone()
            if existing is not None and existing[0] != payload:
                raise ValueError("Conflicting audit snapshot")
            conn.execute(
                "INSERT OR IGNORE INTO llm_usage_audit(query_id,payload) VALUES (?,?)",
                (record.query_id, payload),
            )
            conn.execute(
                "DELETE FROM llm_usage_audit WHERE id NOT IN "
                "(SELECT id FROM llm_usage_audit ORDER BY id DESC LIMIT ?)",
                (self.max_records,),
            )

    def read(self, query_id: str) -> dict[str, Any] | None:
        if not _QUERY_ID.fullmatch(query_id):
            raise ValueError("Invalid audit query id")
        if not self.path.exists():
            return None
        uri = self.path.resolve().as_uri() + "?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as conn:
            _schema(conn, create=False)
            row = conn.execute(
                "SELECT payload FROM llm_usage_audit WHERE query_id=?", (query_id,)
            ).fetchone()
        if row is None:
            return None
        payload: object = json.loads(row[0])
        if (
            not isinstance(payload, dict)
            or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") not in (1, 2)
            or payload.get("query_id") != query_id
        ):
            raise ValueError("Invalid stored audit snapshot")
        if payload["schema_version"] == 2:
            audit_candidate_digests(payload)
        return payload


async def audit_io[T](operation: Callable[[], T]) -> T:
    """Join even on repeated cancellation; never detach a SQLite worker."""
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(task)
        with contextlib.suppress(Exception, asyncio.CancelledError):
            task.result()
        raise
