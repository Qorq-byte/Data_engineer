"""Immutable bundle seals and real read-only local index handles."""

import sqlite3

import pytest

from app.config.settings import settings
from app.harness.mysql_coordination import CoordinationUnavailableError
from app.harness.worker_release import WorkerRelease, validate_profiles, write_release
from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.lancedb_store import LanceDBStore


def profile():
    return [
        {
            "db_id": "fixture",
            "db_type": "mysql",
            "config": {
                "host": "127.0.0.1",
                "port": 12345,
                "user": "fixture",
                "database": "synthetic",
                "password": "private-fixture-password",
            },
        }
    ]


def test_bundle_seals_credentials_and_detects_file_or_key_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "a" * 48)
    index = tmp_path / "index"
    index.mkdir()
    source = index / "version.json"
    source.write_text("fixed")
    bundle = tmp_path / "release.json"
    write_release(profile(), [source], bundle, index_roots=[index])
    assert "private-fixture-password" not in bundle.read_text()
    release = WorkerRelease(bundle)
    assert release.profiles == profile()
    release.verify_metadata()
    with pytest.raises(ValueError):
        write_release(profile(), [source], bundle)
    (index / "extra").touch()
    with pytest.raises(CoordinationUnavailableError):
        release.verify_metadata()
    (index / "extra").unlink()
    source.write_text("changed")
    with pytest.raises(CoordinationUnavailableError):
        release.verify_metadata()
    with pytest.raises(CoordinationUnavailableError):
        WorkerRelease(bundle)
    monkeypatch.setattr(settings, "jwt_secret", "b" * 48)
    with pytest.raises(CoordinationUnavailableError) as error:
        WorkerRelease(bundle)
    assert "private-fixture-password" not in str(error.value)


@pytest.mark.parametrize("invalid", [[], [{}], [profile()[0], profile()[0]], [None]])
def test_invalid_profiles_fail_before_connections(invalid):
    with pytest.raises(ValueError):
        validate_profiles(invalid)


def test_sparse_read_only_handle_searches_and_cannot_mutate(tmp_path):
    path = str(tmp_path / "sparse.sqlite3")
    writer = BM25Index(path)
    writer.index("schema_metadata", [{"doc_id": "one", "text": "orders revenue"}])
    writer.close()
    reader = BM25Index(path, read_only=True)
    try:
        assert reader.search("schema_metadata", "orders")[0]["doc_id"] == "one"
        with pytest.raises(sqlite3.OperationalError):
            reader.clear("schema_metadata")
        with pytest.raises(ValueError):
            reader.ensure_namespace("missing")
        assert reader.count("schema_metadata") == 1
    finally:
        reader.close()


def test_dense_read_only_handle_cannot_create_write_or_recover(tmp_path):
    directory = str(tmp_path / "dense")
    writer = LanceDBStore(3, directory, embedding_id="fixture")
    writer.init_namespaces()
    writer.close()
    reader = LanceDBStore(3, directory, embedding_id="fixture", read_only=True)
    try:
        assert reader.count("schema_metadata") == 0
        for mutation in (
            lambda: reader.insert("schema_metadata", []),
            lambda: reader.delete("schema_metadata", "true"),
            lambda: reader.drop_namespace("schema_metadata"),
            lambda: reader.restore_database("schema_metadata", "fixture", []),
            lambda: reader.init_namespaces(overwrite=True),
        ):
            with pytest.raises(ValueError):
                mutation()
    finally:
        reader.close()
    missing = tmp_path / "missing"
    with pytest.raises(ValueError):
        LanceDBStore(3, str(missing), read_only=True).connect()
    assert not missing.exists()
