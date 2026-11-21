"""Self-check frozen SQL and retrieval labels and score saved outputs."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.evaluation.acceptance_data import (
    business_source_paths,
    load_business_dataset,
    source_paths,
)
from app.evaluation.acceptance_retrieval import bm25_rankings, evaluate_retrieval, load_retrieval
from app.evaluation.acceptance_sql import evaluate_sql, load_sql
from app.evaluation.acceptance_suite import self_check
from app.evaluation.artifacts import protect_inputs, read_json, write_atomic
from app.evaluation.business_quality import check_thresholds, quality_report
from app.evaluation.predictions import fingerprint
from app.evaluation.retrieval_capture import read_rankings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset-manifest", type=Path, help="Frozen business evaluation manifest")
    sql_inputs = parser.add_mutually_exclusive_group()
    sql_inputs.add_argument("--sql-predictions", type=Path)
    sql_inputs.add_argument("--query-capture", type=Path, help="Single request_query HTTP capture")
    parser.add_argument("--rankings", type=Path)
    parser.add_argument(
        "--thresholds", type=Path,
        help="Gate a business holdout report with saved SQL evidence and rankings",
    )
    parser.add_argument("--split", choices=("all", "dev", "holdout"), default="all")
    parser.add_argument("--k", type=int, default=3)
    args = parser.parse_args(argv)
    previous = logging.root.manager.disable
    logging.disable(max(previous, logging.CRITICAL))
    try:
        if not 1 <= args.k <= 1000:
            raise ValueError("Invalid retrieval K")
        sql_input = args.query_capture or args.sql_predictions
        extras = [path for path in (sql_input, args.rankings, args.thresholds) if path is not None]
        if args.query_capture is not None and (
            args.dataset_manifest is None or args.split == "all"
        ):
            raise ValueError("Query capture requires a frozen business dataset and one split")
        if args.thresholds is not None and (
            args.split != "holdout"
            or any(path is None for path in (
                args.dataset_manifest, sql_input, args.rankings,
            ))
        ):
            raise ValueError("Thresholds require business holdout data and both prediction inputs")
        if args.dataset_manifest is not None:
            extras.extend(business_source_paths(args.dataset_manifest))
        protect_inputs(args.output, source_paths() + extras)
        dataset = (None if args.dataset_manifest is None else
                   load_business_dataset(args.dataset_manifest))
        checked = self_check(dataset)
        business = dataset is not None
        if dataset is None:
            sql, retrieval, snapshot = load_sql(), load_retrieval(), None
        else:
            sql, retrieval, snapshot = dataset.sql, dataset.retrieval, dataset.snapshot
        query_capture = None
        if args.query_capture is None:
            predictions = [] if args.sql_predictions is None else read_json(args.sql_predictions)
            sql_result = evaluate_sql(sql, predictions, args.split, snapshot=snapshot)
        else:
            from app.evaluation.plan_comparison import evaluate_query_capture

            assert dataset is not None
            sql_result, query_capture = evaluate_query_capture(
                dataset, read_json(args.query_capture), args.split,
            )
        ranking_capture = None
        ranks: object
        if args.rankings is None:
            ranks = bm25_rankings(retrieval, args.k, args.split, business=business)
        else:
            ranks, ranking_capture = read_rankings(
                read_json(args.rankings), dataset, args.split, args.k,
            )
        retrieval_result = evaluate_retrieval(retrieval, ranks, args.k, args.split,
                                               business=business)
        report = {"profile": checked["profile"], "self_check": checked,
                  "sql": sql_result,
                  "retrieval": retrieval_result,
                  "retrieval_mode": "SQLite FTS5 BM25 lexical baseline" if args.rankings is None
                                    else "saved external rankings",
                  "llm_calls": 0, "embedding_calls": 0, "actual_cost_usd": None}
        if ranking_capture is not None:
            report["retrieval_mode"] = "frozen corpus document hybrid retrieval"
            report["ranking_capture"] = ranking_capture
        if query_capture is not None:
            report["query_capture"] = query_capture
        passed = True
        if dataset is not None:
            quality = quality_report(dataset, sql_result, retrieval_result)
            report["business_quality"] = quality
            if args.thresholds is not None:
                gate = check_thresholds(args.thresholds, dataset, quality)
                if query_capture is not None:
                    gate["capture_complete"] = query_capture["status"] == "completed"
                    gate["passed"] = gate["passed"] and gate["capture_complete"]
                quality["thresholds"] = gate
                passed = gate["passed"]
        write_atomic(args.output, report)
        print(json.dumps({"profile": report["profile"], "split": args.split,
                          "sql_cases": sql_result["case_count"],
                          "retrieval_queries": retrieval_result["query_count"],
                          "report_sha256": fingerprint(report)}))
        return 0 if passed else 1
    except Exception:
        print("Acceptance evaluation failed; output was not replaced.", file=sys.stderr)
        return 2
    finally:
        logging.disable(previous)


if __name__ == "__main__":
    raise SystemExit(main())
