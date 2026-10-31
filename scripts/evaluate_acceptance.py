"""Self-check frozen30 SQL/30 retrieval labels and score saved outputs; no model calls."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.evaluation.acceptance_data import source_paths
from app.evaluation.acceptance_retrieval import bm25_rankings, evaluate_retrieval, load_retrieval
from app.evaluation.acceptance_sql import evaluate_sql, load_sql
from app.evaluation.acceptance_suite import self_check
from app.evaluation.artifacts import protect_inputs, read_json, write_atomic
from app.evaluation.predictions import fingerprint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sql-predictions", type=Path)
    parser.add_argument("--rankings", type=Path)
    parser.add_argument("--split", choices=("all", "dev", "holdout"), default="all")
    parser.add_argument("--k", type=int, default=3)
    args = parser.parse_args(argv)
    previous = logging.root.manager.disable
    logging.disable(max(previous, logging.CRITICAL))
    try:
        if not 1 <= args.k <= 1000:
            raise ValueError("Invalid retrieval K")
        extras = [path for path in (args.sql_predictions, args.rankings) if path is not None]
        protect_inputs(args.output, source_paths() + extras)
        checked = self_check()
        sql, retrieval = load_sql(), load_retrieval()
        predictions = [] if args.sql_predictions is None else read_json(args.sql_predictions)
        # Self-check consistency is separate from saved-prediction correctness.
        sql_result = evaluate_sql(sql, predictions, args.split)
        ranks = (bm25_rankings(retrieval, args.k, args.split) if args.rankings is None
                 else read_json(args.rankings))
        retrieval_result = evaluate_retrieval(retrieval, ranks, args.k, args.split)
        report = {"profile": "synthetic-acceptance-v3", "self_check": checked,
                  "sql": sql_result,
                  "retrieval": retrieval_result,
                  "retrieval_mode": "SQLite FTS5 BM25 lexical baseline" if args.rankings is None
                                    else "saved external rankings",
                  "llm_calls": 0, "embedding_calls": 0, "actual_cost_usd": None}
        write_atomic(args.output, report)
        print(json.dumps({"profile": report["profile"], "split": args.split,
                          "sql_cases": sql_result["case_count"],
                          "retrieval_queries": retrieval_result["query_count"],
                          "report_sha256": fingerprint(report)}))
        return 0
    except Exception:
        print("Acceptance evaluation failed; output was not replaced.", file=sys.stderr)
        return 2
    finally:
        logging.disable(previous)


if __name__ == "__main__":
    raise SystemExit(main())
