"""Review a saved capacity run against frozen limits and deployment evidence."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from app.evaluation.artifacts import parse_json, protect_inputs, read_bounded_bytes, write_atomic
from app.evaluation.capacity_acceptance import load_capacity_plan
from app.evaluation.capacity_review import review_capacity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--http-report", required=True, type=Path)
    parser.add_argument("--limits", required=True, type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    inputs = [args.manifest, args.http_report, args.limits]
    if args.evidence is not None:
        inputs.append(args.evidence)
    try:
        protect_inputs(args.output, inputs)
        http_raw = read_bounded_bytes(args.http_report)
        limits_raw = read_bounded_bytes(args.limits, 16384)
        evidence_raw = None if args.evidence is None else read_bounded_bytes(args.evidence, 16384)
        evidence = None if evidence_raw is None else parse_json(evidence_raw)
        if evidence_raw is not None and not isinstance(evidence, dict):
            raise ValueError("Invalid deployment evidence")
        result = review_capacity(
            load_capacity_plan(args.manifest), parse_json(http_raw),
            hashlib.sha256(http_raw).hexdigest(), parse_json(limits_raw), evidence,
        )
        result["limits_sha256"] = hashlib.sha256(limits_raw).hexdigest()
        result["evidence_sha256"] = (
            hashlib.sha256(evidence_raw).hexdigest() if evidence_raw is not None else None
        )
        write_atomic(args.output, result)
    except (OSError, ValueError):
        print("Capacity review failed; output was not replaced.", file=sys.stderr)
        return 2
    print(json.dumps({"passed": result["passed"], "http_passed": result["http_passed"],
                      "resources_passed": result["resources_passed"], "cost": result["cost"]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
