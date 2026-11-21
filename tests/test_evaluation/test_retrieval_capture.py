"""Check capture and scoring through the CLIs using real temporary indexes."""

from app.evaluation.artifacts import read_json, write_atomic
from scripts.capture_document_rankings import main as capture
from scripts.evaluate_acceptance import main as evaluate
from tests.test_evaluation.test_business_acceptance import business_dataset, freeze  # noqa: F401


def test_capture_replays_scoped_parent_rankings(business_dataset, tmp_path):  # noqa: F811
    corpus_path = tmp_path / "retrieval.json"
    corpus = read_json(corpus_path)
    query = corpus["queries"][1]["query"]
    corpus["documents"][0]["text"] = (query + " " + "invoice amounts " * 800).strip()
    corpus["documents"].extend([
        {"id": "other-db", "db_id": "different", "domain": "accounts", "text": query},
        {"id": "other-domain", "db_id": "billing", "domain": "inventory", "text": query},
        # This input ID shares its text with an index chunk identifier convention.
        {"id": "invoice#0", "db_id": "billing", "domain": "accounts", "text": query},
    ])
    corpus["queries"][2]["db_id"] = "different"
    corpus["queries"][2]["domain"] = "inventory"
    write_atomic(corpus_path, corpus)
    freeze(tmp_path)

    first, second, report = (tmp_path / name for name in ("a.json", "b.json", "report.json"))
    options = ["--dataset-manifest", str(business_dataset), "--split", "holdout", "--k", "3"]
    assert capture([*options, "--chunk-top-k", "40", "--output", str(first)]) == 0
    assert capture([*options, "--chunk-top-k", "40", "--output", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()
    artifact = read_json(first)
    assert artifact["embedding_model"] == "mock-deterministic"
    first_ranking, second_ranking = artifact["rankings"]
    assert first_ranking["id"] == "test-1"
    assert sorted(first_ranking["retrieved"]) == ["contact", "invoice", "invoice#0"]
    assert second_ranking == {"id": "test-2", "retrieved": []}

    scoring = [*options, "--rankings", str(first), "--output", str(report)]
    assert evaluate(scoring) == 0
    result = read_json(report)
    assert result["ranking_capture"]["manifest_sha256"] == artifact["manifest_sha256"]
    assert result["ranking_capture"]["chunk_top_k"] == 40
    assert result["retrieval"]["error_counts"] == {}
    assert result["retrieval"]["answerable_macro"]["recall_at_k"] == 1
    assert result["retrieval"]["no_answer_correct_empty_count"] == 1

    saved = report.read_bytes()
    assert evaluate([*scoring, "--split", "dev"]) == 2
    assert evaluate([*scoring, "--k", "2"]) == 2
    write_atomic(first, {**artifact, "manifest_sha256": "0" * 64})
    assert evaluate(scoring) == 2
    artifact["rankings"].pop()
    write_atomic(first, artifact)
    assert evaluate(scoring) == 2
    assert report.read_bytes() == saved


def test_capture_rejects_input_drift_and_output_alias(business_dataset, tmp_path):  # noqa: F811
    output = tmp_path / "rankings.json"
    output.write_bytes(b"previous rankings")
    options = ["--dataset-manifest", str(business_dataset), "--output", str(output)]
    frozen = business_dataset.read_bytes()
    assert capture([*options, "--output", str(business_dataset)]) == 2
    assert business_dataset.read_bytes() == frozen
    assert capture([*options, "--k", "11"]) == 2
    corpus = tmp_path / "retrieval.json"
    corpus.write_bytes(corpus.read_bytes() + b"\n")
    assert capture(options) == 2
    assert output.read_bytes() == b"previous rankings"
