"""Seal a prebuilt immutable worker generation offline; no database/model calls."""

import argparse
import json
from pathlib import Path

from app.config.settings import settings
from app.harness.worker_release import ROOT, write_release


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(settings.jwt_secret.strip()) < 32:
        raise ValueError("JWT_SECRET must have at least 32 characters to seal credentials")
    if args.profiles.stat().st_size > 1024 * 1024:
        raise ValueError("Profile input exceeds 1 MiB")
    dense, sparse = Path(settings.rag_lancedb_dir), Path(settings.rag_bm25_path)
    if (
        not dense.is_absolute()
        or not sparse.is_absolute()
        or not dense.is_dir()
        or not sparse.is_file()
        or not (dense / "embedding-version.json").is_file()
    ):
        raise ValueError("Explicit absolute paths to prebuilt versioned indexes required")
    if any(dense.parent.glob("index-pending-*.json")):
        raise ValueError("Repair interrupted index replacements offline first")
    files = set(dense.rglob("*"))
    files.update((ROOT / "app/config").rglob("*.yml"))
    files.update((ROOT / "app/workflow/plans").rglob("*.yml"))
    files.update({sparse, Path(settings.agent_config_path).resolve()})
    selected = sorted(
        path
        for path in files
        if path.is_file() and not path.name.endswith(("-wal", "-shm", ".lock"))
    )
    write_release(
        json.loads(args.profiles.read_text(encoding="utf-8")),
        selected,
        args.output,
        index_roots=[dense],
    )
    print("Immutable worker release created; credentials sealed, no external calls")


if __name__ == "__main__":
    main()
