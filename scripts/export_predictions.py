"""Export explicitly bound saved QueryResponse files; no LLM or SQL execution.

python -m scripts.export_predictions --dataset cases.json --responses responses.json
    --bindings bindings.json --output predictions.json [--audit-db usage.sqlite3]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.audit.store import UsageAuditStore
from app.evaluation.predictions import export_predictions, fingerprint

MAX_INPUT_BYTES = 16 * 1024 * 1024


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError("Non-finite JSON constant")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _check_depth(root: object) -> None:
    # Iterator stack bounds extra memory by depth rather than input width.
    stack: list[tuple[Iterator[object], int]] = [(iter((root,)), 0)]
    while stack:
        children, depth = stack[-1]
        try:
            value = next(children)
        except StopIteration:
            stack.pop()
            continue
        if isinstance(value, (dict, list)):
            if depth >= 64:
                raise ValueError("Export input nesting limit exceeded")
            iterator = iter(value.values()) if isinstance(value, dict) else iter(value)
            stack.append((iterator, depth + 1))


def read_json(path: Path) -> object:
    with path.open("rb") as source:
        raw = source.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("Export input size limit exceeded")
    try:
        result: object = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_float=_float,
        )
    except RecursionError:
        raise ValueError("Export input nesting limit exceeded") from None
    _check_depth(result)
    return result


def _protect_inputs(output: Path, inputs: list[Path]) -> None:
    for source in inputs:
        if output.resolve() == source.resolve() or (
            output.exists() and source.exists() and output.samefile(source)
        ):
            raise ValueError("Output must not overwrite an input")


def _write_atomic(output: Path, rows: object) -> None:
    serialized = json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=".prediction-export-",
            suffix=".tmp",
            delete=False,
        ) as target:
            temporary = Path(target.name)
            target.write(serialized)
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit-db", type=Path)
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
        )
        _write_atomic(args.output, rows)
    except (OSError, ValueError, TypeError, sqlite3.Error):
        parser.exit(2, "Prediction export failed; check input contracts and file access.\n")
    print(json.dumps({"prediction_count": len(rows), "predictions_sha256": fingerprint(rows)}))


if __name__ == "__main__":
    main()
