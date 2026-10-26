"""Scoped retrieval qrels, adverse rankings and a real SQLite FTS5 lexical baseline."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from app.evaluation import acceptance_data as data
from app.evaluation.acceptance_sql import predictions_by_id, selected
from app.evaluation.artifacts import read_json
from app.evaluation.inputs import text
from app.evaluation.predictions import fingerprint
from app.evaluation.ranking import ranking_metrics
from app.knowledge.retrieval.bm25_index import BM25Index

METRICS = ("recall_at_k", "precision_at_k", "mrr_at_k", "ndcg_at_k")


def load_retrieval() -> dict[str, Any]:
    return data.validate_retrieval(read_json(data.DIRECTORY / "retrieval.json"))


def score_ranking(
    query: dict[str, Any], ranking: list[str], documents: dict[str, dict[str, Any]], k: int,
    *, missing: bool = False,
) -> dict[str, Any]:
    errors = []
    if missing:
        errors.append("MISSING_PREDICTION")
    if len(set(ranking)) != len(ranking):
        errors.append("DUPLICATE_DOCUMENT")
    if any(identity not in documents for identity in ranking):
        errors.append("UNKNOWN_DOCUMENT")
    if any(any(documents[identity][key] != query[key] for key in ("db_id", "domain"))
           for identity in ranking if identity in documents):
        errors.append("OUT_OF_SCOPE")
    metrics = (dict.fromkeys(METRICS, 0.0) if errors else
               ranking_metrics(ranking, set(query["relevant"]), k))
    return {"id": query["id"], "split": query["split"], "answerable": query["answerable"],
            "retrieved": ranking, "error_codes": errors, "ranking_valid": not errors,
            "no_answer_correct": (not query["answerable"] and not ranking and not errors),
            **metrics}


def retrieval_self_check(suite: object) -> dict[str, Any]:
    suite = data.validate_retrieval(suite)
    documents = {doc["id"]: doc for doc in suite["documents"]}
    killed = []
    for query in suite["queries"]:
        oracle = score_ranking(query, query["relevant"], documents, 3)
        bad = score_ranking(query, query["bad_ranking"], documents, 3)
        if not oracle["ranking_valid"] or (
            oracle["recall_at_k"] != 1 if query["answerable"] else not oracle["no_answer_correct"]
        ):
            raise ValueError("Retrieval labels failed independent consistency check")
        if bad["ranking_valid"] and (
            bad["recall_at_k"] == 1 if query["answerable"] else bad["no_answer_correct"]
        ):
            raise ValueError("Wrong retrieval counterexample survived")
        killed.append({"id": query["id"], "error_codes": bad["error_codes"],
                       "recall_at_k": bad["recall_at_k"],
                       "no_answer_correct": bad["no_answer_correct"]})
    return {"qrels_checked": len(killed), "bad_rankings_rejected": len(killed),
            "self_check_k": 3, "counterexample_details": killed}


def bm25_rankings(suite: object, k: int, split: str = "all") -> list[dict[str, Any]]:
    if type(k) is not int or not 1 <= k <= 1000:
        raise ValueError("Invalid retrieval K")
    suite = data.validate_retrieval(suite)
    queries = selected(suite["queries"], split)
    index = BM25Index()
    try:
        index.index("acceptance", [{"doc_id": doc["id"], "text": doc["text"],
            "metadata": {"db_id": doc["db_id"], "domain": doc["domain"]}}
            for doc in suite["documents"]])
        return [{"id": query["id"], "retrieved": [row["doc_id"] for row in index.search(
            "acceptance", query["query"], k,
            metadata_filter={key: query[key] for key in ("db_id", "domain")})]}
            for query in queries]
    finally:
        index.close()


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [row for row in rows if row["answerable"]]
    no_answer = [row for row in rows if not row["answerable"]]
    return {"query_count": len(rows), "answerable_count": len(answerable),
            "no_answer_count": len(no_answer),
            "all_queries_macro": {key: statistics.mean(row[key] for row in rows)
                                  if rows else None for key in METRICS},
            "answerable_macro": {key: statistics.mean(row[key] for row in answerable)
                                 if answerable else None for key in METRICS},
            "no_answer_correct_empty_count": sum(row["no_answer_correct"] for row in no_answer),
            "no_answer_correct_empty_rate": (sum(row["no_answer_correct"] for row in no_answer)
                                             / len(no_answer) if no_answer else None),
            "invalid_ranking_count": sum(not row["ranking_valid"] for row in rows),
            "error_counts": dict(Counter(code for row in rows for code in row["error_codes"]))}


def evaluate_retrieval(
    suite: object, predictions: object, k: int = 3, split: str = "all",
) -> dict[str, Any]:
    if type(k) is not int or not 1 <= k <= 1000:
        raise ValueError("Invalid retrieval K")
    suite = data.validate_retrieval(suite)
    by_id = predictions_by_id(predictions, suite["queries"])
    for prediction in by_id.values():
        data.fields(prediction, "id retrieved")
        ranking = prediction["retrieved"]
        if not isinstance(ranking, list) or len(ranking) > 1000:
            raise ValueError("Invalid retrieval ranking")
        for identity in ranking:
            # Keep duplicate/unknown IDs as scored violations; never repair them.
            text(identity, 512)
    documents = {doc["id"]: doc for doc in suite["documents"]}
    queries = selected(suite["queries"], split)
    rows = [score_ranking(query, by_id[query["id"]]["retrieved"]
                         if query["id"] in by_id else [], documents, k,
                         missing=query["id"] not in by_id) for query in queries]
    return {"split": split, "k": k, **_summary(rows),
            "prediction_count": sum(query["id"] in by_id for query in queries),
            "provided_prediction_count": len(by_id),
            "predictions_sha256": fingerprint(predictions),
            "by_split": {name: _summary([row for row in rows if row["split"] == name])
                         for name in data.SPLITS}, "details": rows,
            "metric_policy": "Binary qrels; precision divides by K. Empty qrels rank metrics=0. "
                             "Missing no-answer predictions are failures. Invalid rankings score0."}
