"""Export explicitly bound saved QueryResponse files; no LLM or SQL execution.

python -m scripts.export_predictions --dataset cases.json --responses responses.json
    --bindings bindings.json --output predictions.json [--audit-db usage.sqlite3]
"""

from __future__ import annotations

import argparse
import json
import os as os  # Preserve the legacy module export used by replacement hooks.
import sqlite3
from pathlib import Path

from app.audit.store import UsageAuditStore
from app.evaluation.artifacts import protect_inputs as _protect_inputs
from app.evaluation.artifacts import read_json as _read_json
from app.evaluation.artifacts import write_atomic as _write_artifact
from app.evaluation.predictions import export_predictions, fingerprint

MAX_INPUT_BYTES = 16 * 1024 * 1024


def read_json(path: Path) -> object:
    return _read_json(path, max_bytes=MAX_INPUT_BYTES)


def _write_atomic(output: Path, rows: object) -> None:
    _write_artifact(output, rows, prefix=".prediction-export-")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-db", type=Path)
    parser.add_argument("--require-candidate-audit", action="store_true")
    args = parser.parse_args()
    try:
        inputs = [args.dataset, args.responses, args.bindings]
        if args.audit_db is not None:
            inputs.append(args.audit_db)
        _protect_inputs(args.output, inputs)
        lookup = UsageAuditStore(str(args.audit_db)).read if args.audit_db is not None else None
        rows = export_predictions(
            read_json(args.dataset),
            read_json(args.responses),
            read_json(args.bindings),
            audit_lookup=lookup,
            require_candidate_audit=args.require_candidate_audit,
        )
        _write_atomic(args.output, rows)
    except (OSError, ValueError, TypeError, sqlite3.Error):
        parser.exit(2, "Prediction export failed; check input contracts and file access.\n")
    print(json.dumps({"prediction_count": len(rows), "predictions_sha256": fingerprint(rows)}))


if __name__ == "__main__":
    main()
