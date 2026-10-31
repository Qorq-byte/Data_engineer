"""Operator CLI for backing up, verifying, restoring and pruning runtime state.

    python -m scripts.state_backup --action backup --state-root data --backup-root .state_backups
    python -m scripts.state_backup --action verify --backup-dir .state_backups/20261031-160000
    python -m scripts.state_backup --action restore --backup-dir ... --state-root data --overwrite
    python -m scripts.state_backup --action prune --backup-root .state_backups --keep 3

A directory source must be captured while the service is stopped or the index
is not being written; this tool cannot make a live tree copy atomic.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from app.recovery import (
    BackupLimits,
    RecoveryError,
    StateSource,
    backup,
    prune,
    restore,
    verify,
)

DEFAULT_LANCEDB = "lancedb"
DEFAULT_BM25 = "bm25.db"
DEFAULT_AUDIT = "llm_audit.sqlite3"


def sources(state_root: Path, audit_path: str = "") -> list[StateSource]:
    """Declare the persisted fact sources and their restart treatment."""
    audit = Path(audit_path) if audit_path else state_root / DEFAULT_AUDIT
    return [
        StateSource("lancedb", "tree", state_root / DEFAULT_LANCEDB,
                    "rebuild if missing or unverified; readiness stays degraded until re-indexed"),
        StateSource("bm25", "sqlite", state_root / DEFAULT_BM25,
                    "rebuild with the vector index; never migrated alone"),
        StateSource("audit", "sqlite", audit,
                    "optional; absence disables usage audit rather than blocking queries"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", required=True,
                        choices=("backup", "verify", "restore", "prune", "sources"))
    parser.add_argument("--state-root", type=Path, default=Path("data"))
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--backup-root", type=Path, default=Path(".state_backups"))
    parser.add_argument("--audit-path", default="")
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--max-files", type=int, default=20000)
    args = parser.parse_args(argv)
    root = args.state_root.resolve()
    declared = sources(root, args.audit_path)
    try:
        if args.action == "sources":
            print(json.dumps([{"name": s.name, "kind": s.kind, "path": str(s.path),
                               "exists": s.exists(), "restart_policy": s.restart_policy}
                              for s in declared], indent=2, ensure_ascii=False))
            return 0
        if args.action == "backup":
            target = args.backup_dir or args.backup_root / time.strftime("%Y%m%d-%H%M%S")
            manifest = backup(declared, target,
                              BackupLimits(max_bytes=args.max_bytes, max_files=args.max_files))
            print(json.dumps({"backup": str(target), "total_files": manifest["total_files"],
                              "total_bytes": manifest["total_bytes"],
                              "present": [s["name"] for s in manifest["sources"] if s["present"]]},
                             ensure_ascii=False))
            return 0
        if args.backup_dir is None:
            raise RecoveryError("--backup-dir is required for this action")
        if args.action == "verify":
            result = verify(args.backup_dir)
            print(json.dumps({"ok": result["ok"], "checked_files": result["checked_files"],
                              "problems": result["problems"]}, ensure_ascii=False))
            return 0 if result["ok"] else 3
        if args.action == "restore":
            result = restore(args.backup_dir, root, overwrite=args.overwrite)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        removed = prune(args.backup_root, args.keep)
        print(json.dumps({"removed": removed, "kept": args.keep}, ensure_ascii=False))
        return 0
    except (RecoveryError, ValueError, OSError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
