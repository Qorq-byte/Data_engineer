"""Tests for WorkflowTrace.record() and persist() — the auditability invariant.

Covers: record() appending simplified NodeTrace entries, persist() writing
re-parseable JSONL (env-configured and default directories), the empty
workflow_id no-op, and IO-error suppression.
"""

from __future__ import annotations

import json
from datetime import datetime

from app.models.workflow import (
    Evaluation,
    MetricResult,
    NodeTrace,
    WorkflowTrace,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _trace(workflow_id: str = "wf_test_001") -> WorkflowTrace:
    return WorkflowTrace(
        workflow_id=workflow_id,
        plan_id="gensql_agentic",
        session_id="sess-42",
        started_at=datetime(2026, 7, 17, 12, 0, 0),
    )


def _evaluation(passed: bool = True) -> Evaluation:
    return Evaluation(
        passed=passed,
        metrics={
            "syntax_valid": MetricResult(name="syntax_valid", passed=passed, details="checked")
        },
        needs_revision=not passed,
        notes="unit test",
    )


# ── record() ───────────────────────────────────────────────────────────


class TestRecord:
    def test_record_appends_node(self):
        trace = _trace()
        trace.record("schema_linking", "linked 3 tables")

        assert len(trace.nodes) == 1
        node = trace.nodes[0]
        assert isinstance(node, NodeTrace)
        assert node.node_id == "schema_linking"
        assert node.node_name == "schema_linking"
        assert node.output_summary == "linked 3 tables"

    def test_record_sets_completed_at(self):
        trace = _trace()
        trace.record("generate_sql", "1 candidate")

        assert trace.nodes[0].completed_at is not None
        assert isinstance(trace.nodes[0].completed_at, datetime)

    def test_record_stores_evaluation(self):
        trace = _trace()
        evaluation = _evaluation(passed=False)
        trace.record("validate_sql", "validation failed", evaluation=evaluation)

        assert trace.nodes[0].evaluation is evaluation
        assert trace.nodes[0].evaluation.passed is False

    def test_record_without_evaluation_defaults_none(self):
        trace = _trace()
        trace.record("execute_sql", "5 rows")

        assert trace.nodes[0].evaluation is None

    def test_record_preserves_order(self):
        trace = _trace()
        for node_id in ("a", "b", "c"):
            trace.record(node_id, f"out-{node_id}")

        assert [n.node_id for n in trace.nodes] == ["a", "b", "c"]


# ── persist() ──────────────────────────────────────────────────────────


class TestPersist:
    async def test_persist_writes_jsonl_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path))
        trace = _trace()
        trace.record("step1", "ok")
        trace.completed_at = datetime(2026, 7, 17, 12, 0, 5)

        await trace.persist()

        target = tmp_path / "wf_test_001.jsonl"
        assert target.exists()

    async def test_persist_header_line_has_workflow_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path))
        trace = _trace()
        trace.completed_at = datetime(2026, 7, 17, 12, 0, 5)
        await trace.persist()

        header = json.loads(
            (tmp_path / "wf_test_001.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        assert header["workflow_id"] == "wf_test_001"
        assert header["plan_id"] == "gensql_agentic"
        assert header["session_id"] == "sess-42"
        # datetimes serialized as ISO-8601 strings
        assert datetime.fromisoformat(header["started_at"]) == datetime(2026, 7, 17, 12, 0, 0)
        assert datetime.fromisoformat(header["completed_at"]) == datetime(2026, 7, 17, 12, 0, 5)

    async def test_persist_incomplete_trace_has_null_completed_at(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path))
        trace = _trace()
        await trace.persist()

        header = json.loads(
            (tmp_path / "wf_test_001.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        assert header["completed_at"] is None

    async def test_persist_one_line_per_node_and_reparseable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path))
        trace = _trace()
        trace.record("step1", "output one", evaluation=_evaluation())
        trace.record("step2", "output two")

        await trace.persist()

        lines = (tmp_path / "wf_test_001.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3  # header + 2 nodes
        parsed = [json.loads(line) for line in lines]  # all lines valid JSON
        assert parsed[1]["node_id"] == "step1"
        assert parsed[2]["node_id"] == "step2"
        assert parsed[2]["output_summary"] == "output two"

    async def test_persist_serializes_nested_evaluation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path))
        trace = _trace()
        trace.record("validate", "failed", evaluation=_evaluation(passed=False))

        await trace.persist()

        node = json.loads(
            (tmp_path / "wf_test_001.jsonl").read_text(encoding="utf-8").splitlines()[1]
        )
        evaluation = node["evaluation"]
        assert evaluation["passed"] is False
        assert evaluation["needs_revision"] is True
        assert evaluation["metrics"]["syntax_valid"]["name"] == "syntax_valid"
        assert evaluation["metrics"]["syntax_valid"]["passed"] is False

    async def test_persist_node_datetimes_are_iso_strings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path))
        trace = _trace()
        trace.record("step1", "ok")

        await trace.persist()

        node = json.loads(
            (tmp_path / "wf_test_001.jsonl").read_text(encoding="utf-8").splitlines()[1]
        )
        assert isinstance(node["started_at"], str)
        datetime.fromisoformat(node["started_at"])  # must not raise
        datetime.fromisoformat(node["completed_at"])  # must not raise

    async def test_persist_empty_workflow_id_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path))
        trace = _trace(workflow_id="")
        trace.record("step1", "ok")

        await trace.persist()

        assert list(tmp_path.iterdir()) == []

    async def test_persist_creates_nested_directories(self, tmp_path, monkeypatch):
        nested = tmp_path / "deep" / "nested" / "traces"
        monkeypatch.setenv("DE_TRACE_DIR", str(nested))
        trace = _trace()
        trace.record("step1", "ok")

        await trace.persist()

        assert (nested / "wf_test_001.jsonl").exists()

    async def test_persist_default_directory_by_date(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DE_TRACE_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        trace = _trace()
        trace.record("step1", "ok")

        await trace.persist()

        expected = tmp_path / "data" / "traces" / datetime.now().strftime("%Y%m%d")
        assert (expected / "wf_test_001.jsonl").exists()

    async def test_persist_io_error_is_suppressed(self, tmp_path, monkeypatch):
        """Pointing the trace dir at an existing file must not raise."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        monkeypatch.setenv("DE_TRACE_DIR", str(blocker))
        trace = _trace()
        trace.record("step1", "ok")

        await trace.persist()  # must not raise

        assert blocker.read_text(encoding="utf-8") == "not a directory"

    async def test_persist_overwrites_previous_snapshot(self, tmp_path, monkeypatch):
        """Persisting twice keeps a single, current snapshot of the trace."""
        monkeypatch.setenv("DE_TRACE_DIR", str(tmp_path))
        trace = _trace()
        trace.record("step1", "ok")
        await trace.persist()
        trace.record("step2", "ok")
        await trace.persist()

        lines = (tmp_path / "wf_test_001.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3  # header + 2 nodes, not duplicated
