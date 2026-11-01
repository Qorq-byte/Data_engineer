"""Trace ids must stay distinct when several worker processes run at once."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.harness import runner


def test_workflow_id_carries_the_process_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner.os, "getpid", lambda: 4321)
    workflow_id = runner._new_workflow_id()
    assert workflow_id.startswith("wf_")
    assert workflow_id.endswith("_4321")


def test_two_processes_cannot_produce_the_same_trace_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same timestamp, different process: the ids must still differ."""

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            return datetime(2026, 11, 1, 12, 0, 0, 123456)

    monkeypatch.setattr(runner, "datetime", _FixedDatetime)
    monkeypatch.setattr(runner.os, "getpid", lambda: 111)
    first = runner._new_workflow_id()
    monkeypatch.setattr(runner.os, "getpid", lambda: 222)
    second = runner._new_workflow_id()

    assert first == "wf_20261101_120000_123456_111"
    assert second == "wf_20261101_120000_123456_222"
    assert first != second


def test_ids_differ_within_one_process(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([datetime(2026, 11, 1, 12, 0, 0, 1), datetime(2026, 11, 1, 12, 0, 0, 2)])

    class _SteppingDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            return next(clock)

    monkeypatch.setattr(runner, "datetime", _SteppingDatetime)
    assert runner._new_workflow_id() != runner._new_workflow_id()
