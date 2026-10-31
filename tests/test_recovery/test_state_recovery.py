"""Recovery contracts: verified backup/restore and crash-visible index markers.

The subprocess cases start a real lifespan in a new process so that "restart"
is not simulated by re-initialising an in-process singleton.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, MockEmbeddingProvider
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.models.rag_schema import SchemaDocument
from app.recovery import (
    BackupLimits,
    RecoveryError,
    StateSource,
    backup,
    prune,
    restore,
    verify,
)

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "tests" / "fixtures" / "recovery_worker.py"


def _sqlite_fixture(path: Path, rows: int = 3) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        with connection:
            connection.execute("CREATE TABLE audit (id INTEGER PRIMARY KEY, note TEXT)")
            connection.executemany("INSERT INTO audit (note) VALUES (?)",
                                   ((f"row-{index}",) for index in range(rows)))
    finally:
        connection.close()
    return path


def _tree_fixture(root: Path) -> Path:
    (root / "nested").mkdir(parents=True, exist_ok=True)
    (root / "a.json").write_text('{"a": 1}\n', encoding="utf-8")
    (root / "nested" / "b.json").write_text('{"b": 2}\n', encoding="utf-8")
    return root


# ── backup / verify / restore ────────────────────────────────────────────


def test_backup_snapshots_sqlite_and_tree_with_digests(tmp_path):
    state = tmp_path / "state"
    _sqlite_fixture(state / "bm25.db", rows=4)
    _tree_fixture(state / "lancedb")
    sources = [
        StateSource("bm25", "sqlite", state / "bm25.db", "rebuild with the vector index"),
        StateSource("lancedb", "tree", state / "lancedb", "rebuild if unverified"),
        StateSource("audit", "sqlite", state / "missing.db", "optional"),
    ]
    manifest = backup(sources, tmp_path / "backup")
    assert manifest["total_files"] == 3
    present = {row["name"] for row in manifest["sources"] if row["present"]}
    assert present == {"bm25", "lancedb"}
    copied = sqlite3.connect(str(tmp_path / "backup" / "bm25"))
    try:
        assert copied.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 4
    finally:
        copied.close()
    assert verify(tmp_path / "backup")["ok"] is True


def test_backup_refuses_non_empty_destination(tmp_path):
    state = tmp_path / "state"
    _sqlite_fixture(state / "bm25.db")
    destination = tmp_path / "backup"
    destination.mkdir()
    (destination / "stale.txt").write_text("x", encoding="utf-8")
    with pytest.raises(RecoveryError, match="BACKUP_TARGET_NOT_EMPTY"):
        backup([StateSource("bm25", "sqlite", state / "bm25.db", "")], destination)


def test_backup_limit_stops_before_copying_everything(tmp_path):
    state = tmp_path / "state"
    _tree_fixture(state / "lancedb")
    with pytest.raises(RecoveryError, match="BACKUP_FILE_LIMIT_EXCEEDED"):
        backup([StateSource("lancedb", "tree", state / "lancedb", "")],
               tmp_path / "backup", BackupLimits(max_files=1))


def test_verify_reports_tampering_and_missing_without_repairing(tmp_path):
    state = tmp_path / "state"
    _sqlite_fixture(state / "bm25.db")
    _tree_fixture(state / "lancedb")
    sources = [
        StateSource("bm25", "sqlite", state / "bm25.db", ""),
        StateSource("lancedb", "tree", state / "lancedb", ""),
    ]
    destination = tmp_path / "backup"
    backup(sources, destination)
    (destination / "lancedb" / "a.json").write_text('{"a": 999}\n', encoding="utf-8")
    (destination / "lancedb" / "nested" / "b.json").unlink()
    result = verify(destination)
    assert result["ok"] is False
    assert "DIGEST_MISMATCH:lancedb/a.json" in result["problems"]
    assert "MISSING:lancedb/nested/b.json" in result["problems"]
    # Nothing is rewritten by verification itself.
    assert (destination / "lancedb" / "a.json").read_text(encoding="utf-8") == '{"a": 999}\n'


def test_restore_rejects_unverified_backup(tmp_path):
    state = tmp_path / "state"
    _sqlite_fixture(state / "bm25.db")
    destination = tmp_path / "backup"
    backup([StateSource("bm25", "sqlite", state / "bm25.db", "")], destination)
    (destination / "bm25").write_bytes(b"not a database")
    with pytest.raises(RecoveryError, match="RESTORE_REJECTED_UNVERIFIED"):
        restore(destination, tmp_path / "restored")


def test_restore_requires_overwrite_for_existing_target(tmp_path):
    state = tmp_path / "state"
    _sqlite_fixture(state / "bm25.db", rows=2)
    destination = tmp_path / "backup"
    backup([StateSource("bm25", "sqlite", state / "bm25.db", "")], destination)
    target = tmp_path / "restored"
    (target / "bm25").mkdir(parents=True)
    with pytest.raises(RecoveryError, match="RESTORE_TARGET_EXISTS"):
        restore(destination, target)
    result = restore(destination, target, overwrite=True)
    assert result["restored"] == ["bm25"]
    restored = sqlite3.connect(str(target / "bm25"))
    try:
        assert restored.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 2
    finally:
        restored.close()


def test_prune_keeps_newest_manifests_only(tmp_path):
    root = tmp_path / "backups"
    for index in range(4):
        folder = root / f"b{index}"
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(
            json.dumps({"version": 1, "sources": []}), encoding="utf-8")
        os.utime(folder, (1_700_000_000 + index, 1_700_000_000 + index))
    (root / "not-a-backup").mkdir()
    removed = prune(root, keep=2)
    assert removed == ["b1", "b0"]
    assert sorted(path.name for path in root.iterdir()) == ["b2", "b3", "not-a-backup"]


# ── in-flight replacement markers ────────────────────────────────────────


def _rag(tmp_path: Path) -> SchemaMetadataRAG:
    return SchemaMetadataRAG(
        lancedb_store=LanceDBStore(dimension=256, uri=str(tmp_path / "lancedb")),
        bm25_index=BM25Index(db_path=str(tmp_path / "bm25.db")),
        embedding_generator=EmbeddingGenerator(MockEmbeddingProvider()),
        state_dir=tmp_path,
    )


def _documents(count: int = 2) -> list[SchemaDocument]:
    return [SchemaDocument(doc_id=f"probe-{index}", db_id="probe", schema_name="main",
                           table_name=f"table_{index}",
                           embedding_text=f"table table_{index} columns id")
            for index in range(count)]


def test_pending_marker_blocks_trust_on_construction(tmp_path):
    marker = tmp_path / "index-pending-deadbeefdeadbeef.json"
    marker.write_text(json.dumps({"db_id": "probe", "started_unix": 1}), encoding="utf-8")
    rag = _rag(tmp_path)
    assert rag.pending_replacements() == ("probe",)
    assert rag.refresh_required_databases == ("probe",)


async def test_successful_replacement_clears_its_marker(tmp_path):
    rag = _rag(tmp_path)
    await rag.index_schemas(_documents(3), replace_db_id="probe")
    assert list(tmp_path.glob("index-pending-*.json")) == []
    fresh = _rag(tmp_path)
    assert fresh.pending_replacements() == ()


async def test_failed_replacement_keeps_marker_for_next_start(tmp_path):
    rag = _rag(tmp_path)
    with pytest.raises(ValueError):
        await rag.index_schemas(_documents(2), replace_db_id="other")
    markers = list(tmp_path.glob("index-pending-*.json"))
    assert len(markers) == 1
    payload = json.loads(markers[0].read_text(encoding="utf-8"))
    assert payload["db_id"] == "other"
    assert _rag(tmp_path).refresh_required_databases == ("other",)


# ── real subprocess restarts ─────────────────────────────────────────────


def _run_worker(state_root: Path, mode: str,
                documents: int = 5) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, str(WORKER), "--state-root", str(state_root),
         "--mode", mode, "--documents", str(documents)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300, check=False)


def _last_json(output: str) -> dict[str, object]:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            parsed = json.loads(stripped)
            assert isinstance(parsed, dict)
            return parsed
    raise AssertionError(f"no JSON line in worker output: {output[-400:]}")


def test_clean_index_leaves_no_marker_and_reports_counts(tmp_path):
    state = tmp_path / "state"
    result = _run_worker(state, "index", documents=4)
    assert result.returncode == 0, result.stderr[-800:]
    payload = _last_json(result.stdout)
    assert payload["index_counts"] == {"lancedb_count": 4, "bm25_count": 4}
    assert list(state.glob("index-pending-*.json")) == []


def test_crash_during_index_is_visible_after_restart(tmp_path):
    state = tmp_path / "state"
    crashed = _run_worker(state, "index-crash", documents=400)
    assert crashed.returncode == 9, crashed.stderr[-800:]
    markers = list(state.glob("index-pending-*.json"))
    assert len(markers) == 1
    restarted = _run_worker(state, "status")
    assert restarted.returncode == 0, restarted.stderr[-800:]
    payload = _last_json(restarted.stdout)
    rag = payload["rag"]
    assert isinstance(rag, dict)
    assert rag["status"] == "degraded"
    assert rag["refresh_required_database_count"] >= 1
