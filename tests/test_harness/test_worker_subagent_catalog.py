"""Verify release binding, legacy releases and content drift without external services."""

import os

import pytest

from app.config.settings import settings
from app.evaluation.artifacts import write_atomic
from app.harness.mysql_coordination import CoordinationUnavailableError
from app.harness.worker_release import WorkerRelease, write_release
from tests.test_harness.test_worker_release import profile


def _config(tenant, database):
    return {
        "name": "analyst", "tenant_id": tenant, "domain": "ecommerce",
        "database_ids": [database], "capabilities": ["nl_query"], "allowed_users": [str(tenant)],
        "enable_execution": False, "enable_multi_candidate": False,
    }


def test_release_seals_subagents_and_rejects_content_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "a" * 48)
    artifact = tmp_path / "policy.yml"
    artifact.write_text("policy", encoding="utf-8")
    catalog = tmp_path / "subagents.json"
    write_atomic(catalog, {"profile": "shared-subagents-v1", "subagents": [
        _config(1, "fixture"), _config(2, "fixture"),
    ]})
    bundle = tmp_path / "release.json"
    write_release({"1": profile(), "2": profile()}, [artifact], bundle, subagent_catalog=catalog)
    release = WorkerRelease(bundle)
    sealed = release.subagent_catalog
    assert sealed is not None and sealed.sha256 == release.files[str(catalog)]
    assert [(row.tenant_id, row.name) for row in sealed.configs] == [(1, "analyst"), (2, "analyst")]
    assert all(not row.context.enable_execution for row in sealed.configs)
    stamp = catalog.stat()
    catalog.write_bytes(catalog.read_bytes().replace(b'"analyst"', b'"altered"', 1))
    os.utime(catalog, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(CoordinationUnavailableError):
        release.verify_metadata()
    with pytest.raises(CoordinationUnavailableError):
        WorkerRelease(bundle)

    legacy = tmp_path / "legacy.json"
    write_release(profile(), [artifact], legacy)
    assert WorkerRelease(legacy).subagent_catalog is None


@pytest.mark.parametrize("defect", ["tenant", "target", "duplicate", "wildcard", "boolean"])
def test_release_rejects_invalid_or_cross_tenant_subagent_config(tmp_path, monkeypatch, defect):
    monkeypatch.setattr(settings, "jwt_secret", "a" * 48)
    artifact = tmp_path / "policy.yml"
    artifact.write_text("policy", encoding="utf-8")
    catalog = tmp_path / "subagents.json"
    configured = _config(2, "tenant-two")
    rows = [configured]
    if defect == "tenant":
        configured["tenant_id"] = 3
    elif defect == "target":
        configured["database_ids"] = ["fixture"]
    elif defect == "duplicate":
        rows.append({**configured, "name": "Analyst"})
    elif defect == "wildcard":
        configured["database_ids"] = ["*"]
    else:
        configured["enable_execution"] = "false"
    write_atomic(catalog, {"profile": "shared-subagents-v1", "subagents": rows})
    groups = {"1": profile(), "2": [{**profile()[0], "db_id": "tenant-two"}]}
    output = tmp_path / "release.json"
    with pytest.raises(ValueError):
        write_release(groups, [artifact], output, subagent_catalog=catalog)
    assert not output.exists()
