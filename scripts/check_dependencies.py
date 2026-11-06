"""Check direct dependency snapshot coverage and installed versions (no network)."""

from __future__ import annotations

import argparse
import json
import tomllib
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def check_dependencies(
    project: Path,
    constraints: Path,
    installed_version: Callable[[str], str] = version,
    *,
    runtime_only: bool = False,
) -> dict[str, object]:
    """Fail closed on uncovered declarations, stale pins or installation drift."""
    metadata = tomllib.loads(project.read_text(encoding="utf-8"))["project"]
    runtime = [Requirement(item) for item in metadata["dependencies"]]
    dev = [Requirement(item) for item in metadata["optional-dependencies"]["dev"]]
    declared = {canonicalize_name(item.name) for item in runtime + dev}
    pins: dict[str, str] = {}
    errors: list[str] = []
    name: str
    for line in constraints.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        pin = Requirement(line)
        specs = list(pin.specifier)
        name = canonicalize_name(pin.name)
        if (
            pin.url or pin.marker or pin.extras or len(specs) != 1
            or specs[0].operator != "==" or "*" in specs[0].version
        ):
            errors.append(f"INVALID_PIN:{name}")
            continue
        if name in pins:
            errors.append(f"DUPLICATE_PIN:{name}")
        pins[name] = specs[0].version
    for name in sorted(declared - pins.keys()):
        errors.append(f"MISSING_PIN:{name}")
    for name in sorted(pins.keys() - declared):
        errors.append(f"UNDECLARED_PIN:{name}")
    checked = 0
    for requirement in runtime + ([] if runtime_only else dev):
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        name = canonicalize_name(requirement.name)
        pin_version = pins.get(name)
        if pin_version is None:
            continue
        if requirement.url or not requirement.specifier.contains(pin_version):
            errors.append(f"INCOMPATIBLE_PIN:{name}")
        try:
            actual = installed_version(name)
        except PackageNotFoundError:
            errors.append(f"NOT_INSTALLED:{name}")
            continue
        if not Requirement(f"{name}=={pin_version}").specifier.contains(actual):
            errors.append(f"VERSION_MISMATCH:{name}:expected={pin_version}:actual={actual}")
        checked += 1
    return {"ok": not errors, "checked": checked, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-only", action="store_true")
    args = parser.parse_args(argv)
    result = check_dependencies(
        ROOT / "pyproject.toml", ROOT / "constraints.txt", runtime_only=args.runtime_only,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
