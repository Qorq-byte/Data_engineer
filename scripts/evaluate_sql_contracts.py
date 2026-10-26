"""Validate fixed v2 labels and evaluate saved SQL; this command calls no model."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.evaluation.artifacts import protect_inputs, read_json, write_atomic
from app.evaluation.contract_suite import evaluate_suite, load_suite, suite_paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    previous_logging = logging.root.manager.disable
    # Standalone offline CLI: parser warnings may contain the supplied SQL in full.
    logging.disable(max(previous_logging, logging.CRITICAL))
    try:
        inputs = suite_paths() + ([] if args.predictions is None else [args.predictions])
        protect_inputs(args.output, inputs)
        suite = load_suite()
        rows = [] if args.predictions is None else read_json(args.predictions)
        result = evaluate_suite(suite, rows)
        write_atomic(args.output, result)
        print(f"Validated 12 gold checks; robust_matches={result['robust_match_count']}/4")
        return 0
    except Exception:
        print("Contract evaluation failed; output was not replaced.", file=sys.stderr)
        return 2
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())
