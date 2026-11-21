"""Compare saved request_query and reflection responses on a frozen SQLite split."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from app.evaluation.acceptance_data import business_source_paths, load_business_dataset
from app.evaluation.artifacts import protect_inputs, read_json, write_atomic
from app.evaluation.plan_comparison import compare_query_plan_capture, compare_query_plans
from app.evaluation.plan_pilot import attach_pilot_billing, check_pilot_gate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--capture", type=Path, help="Paired artifact from capture_query_plans")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--reflection", type=Path)
    parser.add_argument("--thresholds", type=Path, help="Evaluate frozen holdout pilot limits")
    parser.add_argument("--billing", type=Path, help="Reviewed USD charges keyed by query ID")
    parser.add_argument("--split", choices=("dev", "holdout"), default="holdout")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        inputs = [path for path in (args.capture, args.baseline, args.reflection) if path]
        if len(inputs) != (1 if args.capture else 2):
            raise ValueError("Supply a paired capture or both individual arms")
        if (args.thresholds or args.billing) and not args.capture:
            raise ValueError("Pilot evidence requires a paired capture")
        inputs.extend(path for path in (args.thresholds, args.billing) if path)
        protect_inputs(args.output, [*business_source_paths(args.dataset_manifest), *inputs])
        dataset = load_business_dataset(args.dataset_manifest)
        if args.capture:
            capture = read_json(args.capture)
            if not isinstance(capture, dict):
                raise ValueError("Paired capture must be a JSON object")
            report = compare_query_plan_capture(dataset, capture, args.split)
            if args.billing:
                attach_pilot_billing(report, capture, read_json(args.billing))
            if args.thresholds:
                report["pilot_gate"] = check_pilot_gate(report, capture, read_json(args.thresholds))
        else:
            report = compare_query_plans(
                dataset, read_json(args.baseline), read_json(args.reflection), args.split,
            )
        write_atomic(args.output, report)
    except (OSError, ValueError, sqlite3.Error, RuntimeError):
        print("Plan comparison failed; output was not replaced.", file=sys.stderr)
        return 2
    summary = {
        "profile": report["profile"], "split": report["split"],
        "case_count": report["case_count"], "accuracy_delta": report["accuracy_delta"],
    }
    pilot = report.get("pilot_gate")
    if pilot is not None:
        summary["pilot_passed"] = pilot["passed"]
    print(json.dumps(summary))
    return 1 if pilot is not None and not pilot["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
