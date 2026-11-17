"""Real immutable files, tampering negatives and independent SQLite processes."""

import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI

from app.config.settings import settings
from app.harness.mysql_coordination import CoordinationUnavailableError
from app.harness.worker_release import WorkerRelease, write_release
from tests.test_harness.test_worker_release import profile


@pytest.fixture
def sealed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "synthetic-metric-seal-key-" * 2)
    catalog = tmp_path / "metrics.yml"
    catalog.write_text(
        Path("tests/fixtures/metric_catalog.yml")
        .read_text(encoding="utf-8").replace("metric-db", "fixture"),
        encoding="utf-8",
    )
    source = tmp_path / "config.yml"
    source.write_text("fixed: true\n", encoding="utf-8")
    return catalog, source, tmp_path / "release.json"


def test_catalog_automatically_sealed_and_old_bundle_requires_explicit_opt_in(sealed):
    catalog, source, bundle = sealed
    write_release(profile(), [source], bundle, metric_catalog=catalog)
    release = WorkerRelease(bundle)
    view = release.catalog_for(str(catalog))
    assert view is not None
    assert view.version == "synthetic-ecommerce-v1"
    raw = json.loads(bundle.read_text(encoding="utf-8"))
    assert raw["metric_catalog"]["sha256"] == raw["files"][str(catalog)] == view.sha256
    for value in ("", "relative.yml", str(catalog.parent / "other.yml")):
        with pytest.raises(CoordinationUnavailableError):
            release.catalog_for(value)
    old = bundle.with_name("old.json")
    write_release(profile(), [source], old)
    old_release = WorkerRelease(old)
    assert old_release.catalog_for("") is None
    with pytest.raises(CoordinationUnavailableError):
        old_release.catalog_for(str(catalog))


@pytest.mark.parametrize(
    "part", ["version", "sha256", "files", "metadata_missing", "unknown_field"]
)
def test_invalid_catalog_descriptor_is_rejected(sealed, part):
    catalog, source, bundle = sealed
    write_release(profile(), [source], bundle, metric_catalog=catalog)
    raw = json.loads(bundle.read_text(encoding="utf-8"))
    if part == "files":
        del raw["files"][str(catalog)]
    elif part == "metadata_missing":
        del raw["metric_catalog"]
    elif part == "unknown_field":
        raw["metric_catalog"]["unknown"] = True
    else:
        raw["metric_catalog"][part] = "wrong"
    bundle.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CoordinationUnavailableError):
        WorkerRelease(bundle).catalog_for(str(catalog))


@pytest.mark.parametrize("preserve_metadata", [False, True])
def test_online_catalog_replacement_never_changes_snapshot(sealed, preserve_metadata):
    catalog, source, bundle = sealed
    write_release(profile(), [source], bundle, metric_catalog=catalog)
    release = WorkerRelease(bundle)
    snapshot = release.catalog_for(str(catalog))
    original = catalog.stat()
    catalog.write_text(
        catalog.read_text(encoding="utf-8").replace("aggregation: sum", "aggregation: max"),
        encoding="utf-8",
    )
    if preserve_metadata:
        assert catalog.stat().st_size == original.st_size
        os.utime(catalog, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert snapshot is not None
    assert snapshot.bindings[0].metrics[0].aggregation == "sum"
    with pytest.raises(CoordinationUnavailableError):
        release.catalog_for(str(catalog))


def test_invalid_bindings_output_alias_and_corrupt_catalog_do_not_publish(sealed):
    catalog, source, bundle = sealed
    catalog.write_text(
        catalog.read_text(encoding="utf-8").replace("fixture", "unsealed-target"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        write_release(profile(), [source], bundle, metric_catalog=catalog)
    assert not bundle.exists()
    catalog.write_text("invalid: [", encoding="utf-8")
    with pytest.raises(ValueError):
        write_release(profile(), [source], bundle, metric_catalog=catalog)
    assert not bundle.exists()
    original = source.read_bytes()
    with pytest.raises(ValueError):
        write_release(profile(), [source], source)
    assert source.read_bytes() == original


@pytest.mark.parametrize("target", ["catalog", "bundle"])
def test_deleted_catalog_and_replaced_release_are_rejected(sealed, target):
    catalog, source, bundle = sealed
    write_release(profile(), [source], bundle, metric_catalog=catalog)
    release = WorkerRelease(bundle)
    if target == "catalog":
        catalog.unlink()
    else:
        state = bundle.stat()
        bundle.write_text(bundle.read_text(encoding="utf-8").replace(
            "synthetic-ecommerce-v1", "synthetic-ecommerce-v2"
        ), encoding="utf-8")
        assert bundle.stat().st_size == state.st_size
        os.utime(bundle, ns=(state.st_atime_ns, state.st_mtime_ns))
    with pytest.raises(CoordinationUnavailableError):
        release.catalog_for(str(catalog))


def test_independent_processes_execute_same_sealed_sqlite_definition(sealed, test_sqlite_db):
    catalog, source, bundle = sealed
    write_release(profile(), [source], bundle, metric_catalog=catalog)
    code = """
import json,sqlite3,sys
from pathlib import Path
from app.harness.worker_release import WorkerRelease
catalog=WorkerRelease(Path(sys.argv[1])).catalog_for(sys.argv[2])
selection=catalog.select(version=catalog.version,domain='ecommerce',database='fixture',
    metrics=['revenue','order_count'],dimensions=['region'],filters={})
with sqlite3.connect(sys.argv[3]) as connection:
    rows=connection.execute(selection.sql('sqlite')).fetchall()
print(json.dumps({'version':catalog.version,'sha256':catalog.sha256,'rows':rows}))
"""
    environment = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "USERNAME", "USER",
    }}
    environment.update(
        PYTHONPATH=str(Path.cwd()), JWT_SECRET=settings.jwt_secret, PYTHONUTF8="1",
    )
    outputs = [subprocess.run(
        [sys.executable, "-c", code, str(bundle), str(catalog), test_sqlite_db],
        env=environment, capture_output=True, text=True, timeout=30, check=False,
    ) for _ in range(2)]
    for output in outputs:
        assert output.returncode == 0, output.stderr
    assert outputs[0].stdout == outputs[1].stdout
    assert json.loads(outputs[0].stdout)["rows"]


async def test_unsealed_configuration_rejected_before_database_or_application_start(
    sealed, monkeypatch,
):
    from app.harness.worker_bootstrap import immutable_worker_lifespan

    catalog, source, bundle = sealed
    write_release(profile(), [source], bundle)
    monkeypatch.setattr(settings, "shared_worker_release", str(bundle))
    monkeypatch.setattr(settings, "shared_worker_namespace", "metric-preconnection-test")
    monkeypatch.setattr(settings, "metric_catalog_path", str(catalog))

    @asynccontextmanager
    async def initialize(app):
        pytest.fail("Application initialization must not begin")
        yield

    with pytest.raises(CoordinationUnavailableError, match="Immutable metric catalog"):
        async with immutable_worker_lifespan(FastAPI(), initialize):
            pytest.fail("Invalid configuration must never start serving")
