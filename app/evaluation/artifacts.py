"""Bounded strict JSON and atomic local artifacts shared by offline CLIs."""

import json
import math
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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


def read_json(path: Path, *, max_bytes: int = MAX_INPUT_BYTES) -> object:
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_INPUT_BYTES:
        raise ValueError("Invalid JSON input byte limit")
    with path.open("rb") as source:
        raw = source.read(max_bytes + 1)
    if len(raw) > max_bytes:
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


def protect_inputs(output: Path, inputs: list[Path]) -> None:
    for source in inputs:
        if output.resolve() == source.resolve() or (
            output.exists() and source.exists() and output.samefile(source)
        ):
            raise ValueError("Output must not overwrite an input")


def write_atomic(
    output: Path,
    rows: object,
    *,
    prefix: str = ".offline-report-",
) -> None:
    serialized = json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=prefix,
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
