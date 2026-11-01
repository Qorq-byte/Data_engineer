"""Unit tests for the worker-scaling probe's bookkeeping."""

from __future__ import annotations

import pytest

from scripts.scale_workers import _levels, main, summarise_workers


def _worker(started: float, finished: float, latencies: list[float]) -> dict[str, object]:
    return {
        "pid": 1,
        "started": started,
        "finished": finished,
        "attempts": len(latencies),
        "statuses": {"ok": len(latencies)},
        "latencies_ms": latencies,
    }


def test_summarise_pools_latencies_and_uses_the_shared_window() -> None:
    workers = [
        _worker(100.0, 110.0, [1000.0, 2000.0, 3000.0, 4000.0]),
        _worker(101.0, 111.0, [2000.0, 4000.0, 6000.0, 8000.0]),
    ]
    summary = summarise_workers(workers)
    assert summary["workers"] == 2
    assert summary["attempts"] == 8
    assert summary["statuses"] == {"ok": 8}
    # The window spans the earliest start to the latest finish, not the sum of
    # per-worker durations.
    assert summary["window_seconds"] == 11.0
    assert summary["attempt_rps"] == pytest.approx(8 / 11.0, rel=1e-3)
    # sorted: 1000, 2000, 2000, 3000, 4000, 4000, 6000, 8000
    assert summary["p50_ms"] == pytest.approx(3500.0)
    assert summary["p95_ms"] > 6000.0
    assert summary["per_worker_attempts"] == [4, 4]


def test_a_worker_that_starts_late_cannot_inflate_throughput() -> None:
    workers = [
        _worker(0.0, 10.0, [100.0] * 10),
        _worker(9.0, 10.0, [100.0]),
    ]
    summary = summarise_workers(workers)
    assert summary["window_seconds"] == 10.0
    assert summary["attempt_rps"] == pytest.approx(1.1)


def test_empty_results_are_rejected() -> None:
    with pytest.raises(ValueError):
        summarise_workers([])


def test_worker_counts_are_parsed_and_validated() -> None:
    assert _levels("1,2,4") == [1, 2, 4]
    assert _levels("3") == [3]
    for bad in ("0", "-1", "1,0", ""):
        with pytest.raises(ValueError):
            _levels(bad)


def test_cli_refuses_invalid_worker_counts(tmp_path) -> None:  # type: ignore[no-untyped-def]
    output = tmp_path / "out.json"
    exit_code = main(
        ["--output", str(output), "--workdir", str(tmp_path / "w"), "--workers", "0"]
    )
    assert exit_code == 2
    assert not output.exists()


def test_cli_refuses_an_existing_fixture(tmp_path) -> None:  # type: ignore[no-untyped-def]
    workdir = tmp_path / "w"
    workdir.mkdir(parents=True)
    (workdir / "fixture.sqlite3").write_bytes(b"not a database")
    exit_code = main(
        ["--output", str(tmp_path / "out.json"), "--workdir", str(workdir), "--workers", "1"]
    )
    assert exit_code == 2
