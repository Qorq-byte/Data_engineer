"""Bounded backup, verification and restore of persisted runtime state.

This module owns the operator-facing recovery path for a single worker. It
does not invent consistency: an SQLite source is snapshotted with
``VACUUM INTO`` so the copy cannot contain a half-written page, while a
directory tree is copied and then hashed. A tree source must be captured in
a window where the service is stopped or the index is not being written,
because directory copying is not atomic and this module does not pretend
otherwise.

Restore never trusts leftover files. It verifies every recorded digest first
and reports a mismatch instead of silently copying corrupted bytes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

MANIFEST = "manifest.json"
SOURCE_KIND = Literal["sqlite", "tree", "file"]


class RecoveryError(RuntimeError):
    """A recovery step refused to continue; the message states the reason."""


@dataclass(frozen=True)
class StateSource:
    """One persisted fact source and how it must be treated across restarts."""

    name: str
    kind: SOURCE_KIND
    path: Path
    restart_policy: str

    def exists(self) -> bool:
        return self.path.exists()


@dataclass(frozen=True)
class BackupLimits:
    """Fixed before capture; exceeding either bound aborts the backup."""

    max_bytes: int = 512 * 1024 * 1024
    max_files: int = 20000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _guard(limits: BackupLimits, files: int, size: int) -> None:
    if files > limits.max_files:
        raise RecoveryError(f"BACKUP_FILE_LIMIT_EXCEEDED:{files}>{limits.max_files}")
    if size > limits.max_bytes:
        raise RecoveryError(f"BACKUP_SIZE_LIMIT_EXCEEDED:{size}>{limits.max_bytes}")


def snapshot_sqlite(source: Path, target: Path) -> dict[str, Any]:
    """Copy a live SQLite database without tearing a write in progress."""
    if not source.is_file():
        raise RecoveryError(f"SQLITE_SOURCE_MISSING:{source.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("VACUUM INTO ?", (str(target),))
    except sqlite3.Error as exc:
        raise RecoveryError(f"SQLITE_SNAPSHOT_FAILED:{source.name}:{type(exc).__name__}") from exc
    finally:
        connection.close()
    return {"bytes": target.stat().st_size, "sha256": _sha256(target)}


def backup(sources: list[StateSource], destination: Path,
           limits: BackupLimits | None = None) -> dict[str, Any]:
    """Capture every existing source into a new directory with a digest manifest."""
    limits = limits or BackupLimits()
    if destination.exists() and any(destination.iterdir()):
        raise RecoveryError(f"BACKUP_TARGET_NOT_EMPTY:{destination}")
    destination.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"version": 1, "created_unix": int(time.time()), "sources": []}
    total_files = 0
    total_bytes = 0
    for source in sources:
        if not source.exists():
            manifest["sources"].append({
                "name": source.name, "kind": source.kind, "present": False,
                "restart_policy": source.restart_policy,
            })
            continue
        payload = destination / source.name
        record: dict[str, Any] = {
            "name": source.name, "kind": source.kind, "present": True,
            "restart_policy": source.restart_policy, "files": {},
        }
        if source.kind == "sqlite":
            stats = snapshot_sqlite(source.path, payload)
            total_files += 1
            total_bytes += int(stats["bytes"])
            record["sha256"] = stats["sha256"]
            record["bytes"] = stats["bytes"]
        elif source.kind == "file":
            payload.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source.path, payload)
            size = payload.stat().st_size
            total_files += 1
            total_bytes += size
            record["sha256"] = _sha256(payload)
            record["bytes"] = size
        else:
            if not source.path.is_dir():
                raise RecoveryError(f"TREE_SOURCE_NOT_DIRECTORY:{source.name}")
            files = _tree_files(source.path)
            size = sum(path.stat().st_size for path in files)
            total_files += len(files)
            total_bytes += size
            _guard(limits, total_files, total_bytes)
            shutil.copytree(source.path, payload, dirs_exist_ok=True)
            record["files"] = {path.relative_to(payload).as_posix(): _sha256(path)
                               for path in _tree_files(payload)}
            record["bytes"] = size
        _guard(limits, total_files, total_bytes)
        manifest["sources"].append(record)
    manifest["total_files"] = total_files
    manifest["total_bytes"] = total_bytes
    (destination / MANIFEST).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def verify(directory: Path) -> dict[str, Any]:
    """Recompute every recorded digest; mismatches are reported, never repaired."""
    manifest_path = directory / MANIFEST
    if not manifest_path.is_file():
        raise RecoveryError(f"MANIFEST_MISSING:{directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    checked = 0
    for source in manifest.get("sources", []):
        name = str(source.get("name", ""))
        if not source.get("present"):
            continue
        if source.get("kind") in {"sqlite", "file"}:
            target = directory / name
            if not target.is_file():
                problems.append(f"MISSING:{name}")
                continue
            if _sha256(target) != source.get("sha256"):
                problems.append(f"DIGEST_MISMATCH:{name}")
            checked += 1
            continue
        for relative, digest in (source.get("files") or {}).items():
            target = directory / name / relative
            if not target.is_file():
                problems.append(f"MISSING:{name}/{relative}")
                continue
            if _sha256(target) != digest:
                problems.append(f"DIGEST_MISMATCH:{name}/{relative}")
            checked += 1
    return {"ok": not problems, "checked_files": checked, "problems": problems,
            "manifest": manifest}


def restore(directory: Path, destination: Path, *,
            overwrite: bool = False) -> dict[str, Any]:
    """Verify then copy a backup back into place; refuse on any digest problem."""
    verification = verify(directory)
    if not verification["ok"]:
        raise RecoveryError("RESTORE_REJECTED_UNVERIFIED:" + ",".join(verification["problems"][:5]))
    manifest = verification["manifest"]
    restored: list[str] = []
    for source in manifest.get("sources", []):
        if not source.get("present"):
            continue
        name = str(source["name"])
        payload = directory / name
        target = destination / name
        if target.exists():
            if not overwrite:
                raise RecoveryError(f"RESTORE_TARGET_EXISTS:{target}")
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        if payload.is_dir():
            shutil.copytree(payload, target)
        else:
            shutil.copy2(payload, target)
        restored.append(name)
    return {"restored": restored, "checked_files": verification["checked_files"]}


def prune(root: Path, keep: int) -> list[str]:
    """Delete older backups beyond ``keep``; only directories holding a manifest."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    candidates = sorted((path for path in root.iterdir()
                         if path.is_dir() and (path / MANIFEST).is_file()),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    removed: list[str] = []
    for path in candidates[keep:]:
        shutil.rmtree(path)
        removed.append(path.name)
    return removed


__all__ = [
    "BackupLimits",
    "RecoveryError",
    "StateSource",
    "backup",
    "prune",
    "restore",
    "snapshot_sqlite",
    "verify",
]
