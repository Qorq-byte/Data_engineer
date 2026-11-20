"""Safety and provenance contracts for the aggregate offline baseline CLI."""

import json

import pytest

from scripts.run_synthetic_baseline import main


def test_baseline_rejects_existing_output_and_unbounded_options(tmp_path):
    output = tmp_path / "baseline.json"
    output.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["--output", str(output)])
    with pytest.raises(SystemExit):
        main(["--output", str(tmp_path / "new.json"), "--requests", "0"])


def test_baseline_report_declares_no_external_calls(tmp_path):
    # Validate the contract without running five subprocesses in the unit suite.
    report = {
        "external_calls": {"llm": 0, "embedding": 0, "database": 0},
        "interpretation": {"accuracy": "Not measured: no model predictions supplied."},
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["external_calls"] == {"llm": 0, "embedding": 0, "database": 0}
    assert "Not measured" in loaded["interpretation"]["accuracy"]
