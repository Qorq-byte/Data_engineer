"""Replay the pre-stage-10 linker with current fixed evaluator/labels.

Run from the repo root: python -m scripts.replay_schema_linking_baseline --output before.json
Requires the immutable stage-nine commit in local Git history. Loads trusted
repository Python source into this process only; never changes checked-out files.
A successful replay does not mean its old functional cases passed.
"""

import argparse
import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_REF = "055c265dc4429d7fa8a30fc3be3abc2325194e53"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name, path in [
        ("app.knowledge.schema_retriever", "app/knowledge/schema_retriever.py"),
        ("app.nodes.schema_linking", "app/nodes/schema_linking.py"),
    ]:
        source = subprocess.check_output(
            ["git", "show", f"{BASELINE_REF}:{path}"], cwd=ROOT, encoding="utf-8"
        )
        spec = importlib.util.spec_from_loader(name, loader=None)
        if spec is None:
            raise RuntimeError(f"Cannot create baseline module {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        exec(compile(source, f"{BASELINE_REF}:{path}", "exec"), module.__dict__)

    # Import after the original production modules have been loaded.
    from scripts.evaluate_schema_linking import evaluate

    dataset = json.loads(
        (ROOT / "tests/fixtures/schema_linking_eval.json").read_text(encoding="utf-8")
    )
    result = asyncio.run(evaluate(dataset))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "details"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
