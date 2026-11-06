"""An immutable single-host deployment bundle with sealed connection credentials."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.harness.mysql_coordination import CoordinationUnavailableError
from app.security.crypto import decrypt_secret, encrypt_secret

ROOT = Path(__file__).resolve().parents[2]


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def validate_profiles(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        raise ValueError("One to 64 immutable connections required")
    seen = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"db_id", "db_type", "config"}:
            raise ValueError("Invalid immutable connection profile")
        identity = item["db_id"]
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity):
            raise ValueError("Invalid immutable connection identity")
        if identity in seen or item["db_type"] not in ("mysql", "postgresql"):
            raise ValueError("Duplicate or unsupported immutable connection")
        seen.add(identity)
        config = item["config"]
        if not isinstance(config, dict) or not all(
            isinstance(config.get(key), str) and config[key] for key in ("host", "user", "database")
        ):
            raise ValueError("Invalid immutable connection configuration")
        port = config.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid immutable connection port")
        if not isinstance(config.get("password"), str):
            raise ValueError("Explicit immutable connection password required")
    return value


class WorkerRelease:
    def __init__(self, path: Path) -> None:
        try:
            if not path.is_absolute() or path.stat().st_size > 1024 * 1024:
                raise ValueError("Invalid bundle path or size")
            encoded = path.read_bytes()
            raw = json.loads(encoded)
            if not isinstance(raw, dict) or raw.get("format_version") != 1:
                raise ValueError("Invalid bundle format")
            files = raw["files"]
            if not isinstance(files, dict) or not 1 <= len(files) <= 4096:
                raise ValueError("Invalid bundle files")
            for name, digest in files.items():
                if (
                    not isinstance(name, str)
                    or not Path(name).is_absolute()
                    or not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)
                ):
                    raise ValueError("Invalid bundle file digest")
            plaintext = decrypt_secret(raw["connections_enc"])
            if plaintext is None:
                raise ValueError("Bundle credentials cannot be decrypted")
            self.profiles = validate_profiles(json.loads(plaintext))
            self.path = path
            self.files: dict[str, str] = files
            roots = raw.get("index_roots", [])
            if not isinstance(roots, list) or not all(
                isinstance(name, str) and Path(name).is_absolute() for name in roots
            ):
                raise ValueError("Invalid index roots")
            self.index_roots = [Path(name) for name in roots]
            self.digest = hashlib.sha256(encoded).hexdigest()
            self._metadata: dict[str, tuple[int, int]] = {}
            self.verify_contents()
        except Exception:
            raise CoordinationUnavailableError("Immutable worker release is invalid") from None

    def verify_contents(self) -> None:
        try:
            if _digest(self.path) != self.digest:
                raise ValueError("Release bundle changed during verification")
            for name, expected in self.files.items():
                path = Path(name)
                before = path.stat()
                if _digest(path) != expected:
                    raise ValueError("Release content changed")
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("Release changed during verification")
                self._metadata[name] = after.st_size, after.st_mtime_ns
            self._metadata[str(self.path)] = self.path.stat().st_size, self.path.stat().st_mtime_ns
            self.verify_metadata()
        except Exception:
            raise CoordinationUnavailableError("Immutable worker files changed") from None

    def verify_metadata(self) -> None:
        """Detect ordinary drift; OS administrators must still honor offline maintenance."""
        try:
            for root in self.index_roots:
                actual = {
                    str(path.resolve())
                    for path in root.rglob("*")
                    if path.is_file() and not path.name.endswith(("-wal", "-shm", ".lock"))
                }
                expected_files = {name for name in self.files if Path(name).is_relative_to(root)}
                if actual != expected_files:
                    raise ValueError("Index inventory changed")
            for name, expected in self._metadata.items():
                state = Path(name).stat()
                if (state.st_size, state.st_mtime_ns) != expected:
                    raise ValueError("Release metadata changed")
        except Exception:
            raise CoordinationUnavailableError("Immutable worker files changed") from None


def write_release(
    profiles: object,
    files: list[Path],
    output: Path,
    *,
    index_roots: list[Path] | None = None,
) -> None:
    """Offline-only construction. Does not connect to databases or call embedding models."""
    checked = validate_profiles(profiles)
    if not 1 <= len(files) <= 4096 or output.exists():
        raise ValueError("New output path and bounded file list required")
    payload = {
        "format_version": 1,
        "connections_enc": encrypt_secret(json.dumps(checked, ensure_ascii=False)),
        "files": {str(path.resolve()): _digest(path) for path in files},
        "index_roots": [str(path.resolve()) for path in (index_roots or [])],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Worker release exceeds 1 MiB")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(encoded)
