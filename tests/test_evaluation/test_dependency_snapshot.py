"""Snapshot checks catch drift without using the network or host package versions."""

from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from scripts.check_dependencies import check_dependencies


def check(tmp_path: Path, pins: str, versions: dict[str, str], **kwargs):
    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\ndependencies = ["Example_Pkg[extra]>=1", '
        '"other>=2; python_version < \'1\'"]\n'
        '[project.optional-dependencies]\ndev = ["tool==3"]\n', encoding="utf-8",
    )
    constraints = tmp_path / "constraints.txt"
    constraints.write_text(pins, encoding="utf-8")

    def installed(name):
        if name not in versions:
            raise PackageNotFoundError(name)
        return versions[name]

    return check_dependencies(project, constraints, installed, **kwargs)


PINS = "example-pkg==1.0\nother==2\ntool==3\n"


def test_normalized_names_extras_and_platform_markers(tmp_path):
    result = check(tmp_path, PINS, {"example-pkg": "1.0.0", "tool": "3"})
    assert result == {"ok": True, "checked": 2, "errors": []}


@pytest.mark.parametrize(("pins", "error"), [
    (PINS.replace("tool==3\n", ""), "MISSING_PIN:tool"),
    (PINS + "stale==1\n", "UNDECLARED_PIN:stale"),
    (PINS + "tool==3\n", "DUPLICATE_PIN:tool"),
    (PINS.replace("tool==3", "tool>=3"), "INVALID_PIN:tool"),
    (PINS.replace("tool==3", "tool==3.*"), "INVALID_PIN:tool"),
    (PINS.replace("example-pkg==1.0", "example-pkg==0.5"), "INCOMPATIBLE_PIN:example-pkg"),
])
def test_snapshot_drift_fails(tmp_path, pins, error):
    result = check(tmp_path, pins, {"example-pkg": "1.0", "tool": "3"})
    assert not result["ok"]
    assert error in result["errors"]


def test_installed_drift_and_missing_package(tmp_path):
    result = check(tmp_path, PINS, {"example-pkg": "2"})
    assert result["errors"] == [
        "VERSION_MISMATCH:example-pkg:expected=1.0:actual=2", "NOT_INSTALLED:tool",
    ]


def test_runtime_install_does_not_require_dev_tools(tmp_path):
    assert check(tmp_path, PINS, {"example-pkg": "1"}, runtime_only=True)["ok"]
