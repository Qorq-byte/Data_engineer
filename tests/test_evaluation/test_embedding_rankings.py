"""Synthetic controls exercise actual backends, never assert semantic model quality."""

import json

import pytest

from app.evaluation.embedding_batch import DIMENSION, load_inputs, prepare, text_sha
from app.evaluation.embedding_rankings import ReplayProvider, measure_rankings, validate_rows
from app.evaluation.embedding_runtime import MeasurementError, vector_artifact
from app.knowledge.retrieval.embedding import MockEmbeddingProvider


@pytest.fixture
def manifest():
    return prepare("offline-control", {"provider": "ollama", "name": "embeddinggemma:latest",
        "dimension": DIMENSION, "digest": "sha256:" + "a" * 64,
        "layer_digest": "sha256:" + "b" * 64, "layer_bytes": 621867104})


@pytest.fixture
async def artifact(manifest):
    _, inputs = load_inputs()
    provider = MockEmbeddingProvider(dimension=DIMENSION)
    result = await provider.embed([row["text"] for row in inputs])
    return vector_artifact(manifest, [{"id": row["id"], "text_sha256": text_sha(row["text"]),
                                      "vector": vector}
        for row, vector in zip(inputs, result.vectors, strict=True)])


async def test_synthetic_control_runs_all_real_backends_and_fixed_harness(
        manifest, artifact, tmp_path):
    suite, _ = load_inputs()
    result = await measure_rankings(manifest, artifact, tmp_path, suite)
    assert result["technical_errors"] == []
    assert result["index_ready"]
    assert result["index_counts"] == {"lancedb_count": 23, "bm25_count": 23}
    for mode in ("bm25", "dense", "hybrid"):
        assert result["modes"][mode]["query_count"] == 30
        assert result["modes"][mode]["prediction_count"] == 30
        assert result["modes"][mode]["invalid_ranking_count"] == 0
        assert result["latency"][mode]["attempts"] == 90
        assert result["latency"][mode]["failures"] == 0
    assert result["probe"]["status"] == "passed"
    assert json.loads((tmp_path / "measurement.json").read_text(encoding="utf-8")) == result
    before = (tmp_path / "index/identity.json").read_bytes()
    with pytest.raises(FileExistsError):
        await measure_rankings(manifest, artifact, tmp_path, suite)
    assert (tmp_path / "index/identity.json").read_bytes() == before


async def test_replay_rejects_unknown_text_without_fallback(manifest, artifact):
    provider = ReplayProvider(artifact, manifest)
    with pytest.raises(MeasurementError, match="UNAPPROVED_REPLAY_INPUT"):
        await provider.embed(["not frozen"])


@pytest.mark.parametrize("defect", ["duplicate", "unknown", "short", "wrong_db", "namespace"])
def test_raw_results_rejected_before_fusion(defect):
    docs = {"a.orders": {"db_id": "shop_a", "domain": "sales"}}
    row = {"doc_id": "a.orders", "db_id": "shop_a", "schema_name": "main",
           "table_name": "orders"}
    rows = [row]
    if defect == "duplicate":
        rows.append(dict(row))
    elif defect == "unknown":
        row["doc_id"] = "unknown"
    elif defect == "short":
        rows.clear()
    elif defect == "wrong_db":
        row["db_id"] = "shop_b"
    else:
        row["schema_name"] = "sales"
    with pytest.raises(MeasurementError):
        validate_rows(rows, docs, "shop_a", "sales", 3, dense=True)


async def test_failure_keeps_denominator_and_records_actual_error(
        manifest, artifact, tmp_path, monkeypatch):
    from app.evaluation.embedding_rankings import ScopedDense

    def fail(*args, **kwargs):
        raise MeasurementError("INJECTED_BACKEND_FAILURE")
    monkeypatch.setattr(ScopedDense, "search", fail)
    suite, _ = load_inputs()
    result = await measure_rankings(manifest, artifact, tmp_path, suite)
    assert result["technical_errors"]
    assert result["modes"]["dense"]["prediction_count"] == 0
    assert result["modes"]["dense"]["query_count"] == 30
    assert result["modes"]["dense"]["invalid_ranking_count"] == 30
    assert result["latency"]["dense"]["failures"] == 90
