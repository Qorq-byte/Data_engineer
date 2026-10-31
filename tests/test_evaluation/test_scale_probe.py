"""Scale probe contracts: synthetic size, denominators, stop policy and RSS sampling."""

import asyncio
import time

import pytest

from app.evaluation.scale_probe import (
    ProbeLimits,
    ScaleSpec,
    build_synthetic_database,
    current_rss_bytes,
    directory_bytes,
    environment_facts,
    latency_summary,
    measure_level,
    percentile,
    synthetic_ddl,
    synthetic_documents,
    synthetic_schema,
)


def test_spec_rejects_non_positive_and_keyless_shapes():
    with pytest.raises(ValueError):
        ScaleSpec(tables=0, columns_per_table=4, documents=1)
    with pytest.raises(ValueError):
        ScaleSpec(tables=1, columns_per_table=0, documents=1)
    with pytest.raises(ValueError):
        ScaleSpec(tables=1, columns_per_table=1, documents=1)


def test_spec_counts_tables_separately_from_documents():
    spec = ScaleSpec(tables=7, columns_per_table=3, documents=2)
    described = spec.describe()
    assert described["tables"] == 7
    assert described["column_objects"] == 21
    assert described["documents"] == 2


def test_synthetic_schema_has_one_primary_key_and_requested_width():
    spec = ScaleSpec(tables=3, columns_per_table=5, documents=3)
    plan = synthetic_schema(spec)
    assert [name for name, _ in plan] == ["entity_00000", "entity_00001", "entity_00002"]
    for _, columns in plan:
        assert len(columns) == 5
        assert columns[0] == ("id", "INTEGER PRIMARY KEY")
        assert [name for name, _ in columns[1:]] == ["col_000", "col_001", "col_002", "col_003"]
    assert len(synthetic_ddl(spec)) == 3


def test_synthetic_documents_are_capped_by_document_count():
    spec = ScaleSpec(tables=5, columns_per_table=2, documents=5)
    documents = synthetic_documents(spec)
    assert len(documents) == 5
    assert {row["db_id"] for row in documents} == {"scale-fixture"}
    assert documents[0]["text"].startswith("table entity_00000 columns")


def test_build_synthetic_database_creates_real_tables(tmp_path):
    spec = ScaleSpec(tables=12, columns_per_table=4, documents=12)
    path = tmp_path / "fixture.sqlite3"
    result = build_synthetic_database(path, spec)
    assert result["tables"] == 12
    assert result["bytes"] > 0
    with pytest.raises(FileExistsError):
        build_synthetic_database(path, spec)


def test_percentile_uses_nearest_rank_over_full_sample():
    assert percentile([], 0.5) is None
    assert percentile([7.0], 0.99) == 7.0
    values = [float(n) for n in range(1, 101)]
    assert percentile(values, 0.50) == 50.0
    assert percentile(values, 0.95) == 95.0
    assert percentile(values, 0.99) == 99.0


def test_latency_summary_keeps_failed_attempts_in_denominator():
    summary = latency_summary([10.0, 20.0, 1000.0], [10.0, 20.0])
    assert summary["attempts"] == 3
    assert summary["successful"] == 2
    assert summary["all"]["p99_ms"] == 1000.0
    assert summary["success"]["p99_ms"] == 20.0


async def test_measure_level_counts_every_attempt_and_status():
    import itertools

    sequence = itertools.count()
    lock = asyncio.Lock()

    async def alternating() -> str:
        async with lock:
            index = next(sequence)
        await asyncio.sleep(0)
        return "ok" if index % 2 == 0 else "error"

    level = await measure_level("demo", 2, 10, alternating,
                                limits=ProbeLimits(scenario_seconds=30.0),
                                batch_started=time.perf_counter())
    assert level["requests_planned"] == 10
    assert level["attempts"] == 10
    assert level["not_attempted"] == 0
    assert level["statuses"] == {"ok": 5, "error": 5}
    assert level["latency"]["attempts"] == 10
    assert level["stop_reason"] is None


async def test_measure_level_records_exception_type_not_a_success():
    async def boom() -> str:
        raise RuntimeError("synthetic failure")

    level = await measure_level("boom", 1, 3, boom,
                                limits=ProbeLimits(scenario_seconds=30.0),
                                batch_started=time.perf_counter())
    assert level["statuses"] == {"RuntimeError": 3}
    assert level["latency"]["successful"] == 0
    assert level["latency"]["all"]["p50_ms"] is not None


async def test_scenario_deadline_preserves_partial_samples():
    async def slow() -> str:
        await asyncio.sleep(0.05)
        return "ok"

    limits = ProbeLimits(scenario_seconds=0.01)
    level = await measure_level("deadline", 1, 5, slow,
                                limits=limits, batch_started=time.perf_counter())
    assert level["stop_reason"] == "SCENARIO_DEADLINE"
    assert level["attempts"] + level["not_attempted"] == 5
    assert level["attempts"] < 5


async def test_rss_limit_trips_before_unbounded_growth():
    async def noop() -> str:
        return "ok"

    limits = ProbeLimits(scenario_seconds=30.0, rss_bytes=1)
    level = await measure_level("rss", 1, 3, noop, limits=limits,
                                batch_started=time.perf_counter())
    if current_rss_bytes() is None:
        assert level["stop_reason"] is None
    else:
        assert level["stop_reason"] == "RSS_LIMIT"
        assert level["not_attempted"] == 3


def test_environment_reports_whether_rss_is_readable():
    facts = environment_facts()
    assert facts["python"].count(".") >= 1
    assert facts["rss_probe"] in {"GetProcessMemoryInfo", "/proc/self/status"}
    assert isinstance(facts["rss_readable"], bool)
    rss = current_rss_bytes()
    assert rss is None or rss > 0


def test_directory_bytes_counts_real_files(tmp_path):
    (tmp_path / "a.json").write_text("x" * 10, encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.json").write_text("y" * 5, encoding="utf-8")
    assert directory_bytes(tmp_path) == 15
