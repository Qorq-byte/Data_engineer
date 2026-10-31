"""Run fixture-only tests in owned ephemeral containers, then verify and remove only those IDs."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from app.evaluation.artifacts import write_atomic

ROOT = Path(__file__).resolve().parents[1]
LABEL = "data-engineer.stage40.run"
IMAGES = {"postgresql": "postgres:16", "mysql": "mysql:8.4"}
PASSWORD = "stage40-disposable-admin"  # Public disposable fixture account, never a host credential.


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          check=check, timeout=60)


def validate_owned(metadata: object, identity: str, token: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", identity) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("Full container identity and run token required")
    if not isinstance(metadata, dict):
        raise ValueError("Invalid container metadata")
    config = metadata.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if (metadata.get("Id") != identity or not isinstance(labels, dict)
            or labels.get(LABEL) != token):
        raise RuntimeError("Container ownership mismatch; refusing cleanup")
    return metadata


def inspect_owned(identity: str, token: str) -> dict[str, Any]:
    metadata = json.loads(docker("inspect", identity).stdout)[0]
    return validate_owned(metadata, identity, token)


def test_environment(ports: dict[str, str]) -> dict[str, str]:
    keep = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
        "USERNAME", "USER", "LOGNAME", "LNAME", "SSL_CERT_FILE", "SSL_CERT_DIR",
    }}
    return dict(keep, DE_REAL_DB_TESTS="1", DE_REAL_PG_PORT=ports["postgresql"],
                DE_REAL_MYSQL_PORT=ports["mysql"], LLM_MOCK_MODE="1", PYTHONUTF8="1",
                PYTHONPATH=str(ROOT), LITELLM_LOCAL_MODEL_COST_MAP="True",
                RAG_EMBEDDING_PROVIDER="mock")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/real-db-acceptance/report.json")
    parser.add_argument("--filter", default="")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    owned: list[str] = []
    report: dict[str, Any] = {"status": "starting", "services": {}, "cleanup": [],
                             "scope": "Disposable synthetic databases; no business or model calls"}
    result = 2
    try:
        if docker("info", "--format", "{{.OSType}}").stdout.strip() != "linux":
            raise RuntimeError("Linux test containers are required")
        ports = {}
        for kind, image in IMAGES.items():
            image_id = docker("image", "inspect", image, "--format", "{{.Id}}").stdout.strip()
            if not image_id.startswith("sha256:"):
                raise RuntimeError("Invalid test image identity")
            port = "5432" if kind == "postgresql" else "3306"
            volume = "/var/lib/postgresql/data" if kind == "postgresql" else "/var/lib/mysql"
            env = (["POSTGRES_USER=stage40_admin", f"POSTGRES_PASSWORD={PASSWORD}",
                    "POSTGRES_DB=de_stage40_fixture"] if kind == "postgresql" else
                   [f"MYSQL_ROOT_PASSWORD={PASSWORD}", "MYSQL_DATABASE=de_stage40_fixture",
                    "MYSQL_ROOT_HOST=%"])
            command = ["run", "--detach", "--pull=never", "--name",
                       f"de-stage40-{kind}-{token[:12]}",
                       "--label", f"{LABEL}={token}", "--memory", "768m" if kind == "postgresql"
                       else "1024m", "--cpus", "1", "--pids-limit", "256", "--tmpfs",
                       f"{volume}:rw,nosuid,size=512m", "--publish", f"127.0.0.1::{port}"]
            for item in env:
                command.extend(["--env", item])
            command.append(image_id)
            if kind == "mysql":
                command.extend(["--performance-schema=OFF", "--innodb-buffer-pool-size=64M"])
            identity = docker(*command).stdout.strip()
            # Store the full immutable ID immediately; never delete by name or pattern.
            owned.append(identity)
            metadata = inspect_owned(identity, token)
            binding = metadata["NetworkSettings"]["Ports"][f"{port}/tcp"][0]
            if binding["HostIp"] != "127.0.0.1":
                raise RuntimeError("Test services must bind loopback only")
            ports[kind] = binding["HostPort"]
            report["services"][kind] = {"image": image, "image_id": image_id,
                                       "container_id": identity, "port": int(binding["HostPort"])}
            ready = (["pg_isready", "-h", "127.0.0.1", "-U", "stage40_admin", "-d",
                      "de_stage40_fixture"] if kind == "postgresql" else
                     ["mysqladmin", "--protocol=tcp", "-h", "127.0.0.1", "-uroot",
                      f"-p{PASSWORD}", "ping", "--silent"])
            deadline = time.monotonic() + 150
            while docker("exec", identity, *ready, check=False).returncode != 0:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Disposable service startup deadline exceeded")
                time.sleep(1)
        environment = test_environment(ports)
        command = [sys.executable, "-m", "pytest", str(ROOT / "tests/real_db"),
                   "-c", str(ROOT / "pyproject.toml"), "-q", "--strict-markers",
                   f"--junitxml={output.with_suffix('.junit.xml')}"]
        if args.filter:
            command.extend(["-k", args.filter])
        with tempfile.TemporaryDirectory(
            prefix="stage40-workspace-", dir=output.parent,
        ) as workspace:
            if not Path(workspace).resolve().is_relative_to(output.parent.resolve()):
                raise RuntimeError("Unexpected test workspace location")
            with output.with_suffix(".pytest.txt").open("w", encoding="utf-8") as log:
                completed = subprocess.run(command, cwd=workspace, env=environment, stdout=log,
                                           stderr=subprocess.STDOUT, timeout=300, check=False)
        result = completed.returncode
        report["status"] = "passed" if result == 0 else "failed"
        report["pytest_exit_code"] = result
        xml = ET.parse(output.with_suffix(".junit.xml")).getroot()
        report["server_versions"] = {p.attrib["name"]: p.attrib["value"]
                                     for p in xml.findall(".//property")
                                     if p.attrib.get("name", "").startswith("server_")}
        cases = xml.findall(".//testcase")
        report["tests_by_service"] = {
            kind: sum(bool(re.search(rf"(?:\[|-){kind}(?:-|\])", c.attrib.get("name", "")))
                      for c in cases) for kind in IMAGES
        }
        report["test_count"] = len(cases)
    except (Exception, KeyboardInterrupt):
        report["status"] = "stopped"
        result = 2
    finally:
        # docker run can create a container before returning a startup error. Discover
        # this run's tagged IDs too; each is still inspected before any removal.
        try:
            discovered = docker("ps", "--all", "--quiet", "--no-trunc", "--filter",
                                f"label={LABEL}={token}").stdout.splitlines()
            owned.extend(identity for identity in discovered if identity not in owned)
        except Exception:
            report["status"] = "cleanup_failed"
            result = 2
        for identity in reversed(owned):
            try:
                inspect_owned(identity, token)
                docker("rm", "--force", identity)
                remaining = docker("inspect", identity, check=False)
                removed = remaining.returncode != 0
                report["cleanup"].append({"container_id": identity, "removed": removed})
                if not removed:
                    report["status"] = "cleanup_failed"
                    result = 2
            except Exception:
                report["cleanup"].append({"container_id": identity, "removed": False})
                report["status"] = "cleanup_failed"
                result = 2
        write_atomic(output, report)
    print(f"Disposable database tests: {report['status']}; owned_containers={len(owned)}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
