"""Bounded configuration for the subagent actions of an immutable worker release."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.evaluation.artifacts import parse_json, read_bounded_bytes
from app.models.subagent import AccessControl, SubagentConfig, SubagentContext

_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
_USER = re.compile(r"(?:\*|[1-9][0-9]{0,18})")
_MAX_BYTES = 256 * 1024


@dataclass(frozen=True)
class SealedSubagentCatalog:
    path: Path
    sha256: str
    configs: tuple[SubagentConfig, ...]

    def verify_contents(self) -> None:
        if self.sha256 != hashlib.sha256(read_bounded_bytes(self.path, _MAX_BYTES)).hexdigest():
            raise ValueError("Sealed subagent catalog changed")


def _names(value: object, limit: int, pattern: re.Pattern[str]) -> list[str]:
    if not isinstance(value, list) or len(value) > limit or any(
        not isinstance(item, str) or not pattern.fullmatch(item) for item in value
    ):
        raise ValueError("Invalid subagent names or access list")
    if len(set(value)) != len(value):
        raise ValueError("Duplicate subagent names or access entries")
    return list(value)


def _config(row: Any, targets: dict[int, set[str]]) -> SubagentConfig:
    required = {"name", "tenant_id", "domain", "database_ids", "capabilities", "allowed_users",
                "enable_execution", "enable_multi_candidate"}
    if not isinstance(row, dict) or set(row) != required:
        raise ValueError("Invalid sealed subagent fields")
    name, tenant, domain = row["name"], row["tenant_id"], row["domain"]
    if (
        type(tenant) is not int or tenant not in targets
        or not isinstance(name, str) or not _IDENTIFIER.fullmatch(name)
        or not isinstance(domain, str) or not _IDENTIFIER.fullmatch(domain)
        or type(row["enable_execution"]) is not bool
        or type(row["enable_multi_candidate"]) is not bool
    ):
        raise ValueError("Invalid subagent identity or execution policy")
    databases = _names(row["database_ids"], 64, _IDENTIFIER)
    capabilities = _names(row["capabilities"], 2, _IDENTIFIER)
    if not databases or not set(databases) <= targets[tenant]:
        raise ValueError("Subagent references an unsealed tenant database")
    if not capabilities or not set(capabilities) <= {"nl_query", "metric_query"}:
        raise ValueError("Unsupported sealed subagent capability")
    return SubagentConfig(
        name=name, tenant_id=tenant, domain=domain, capabilities=capabilities,
        workflow_enabled=True,
        context=SubagentContext(
            domain=domain, databases=[{"db_id": db_id} for db_id in databases],
            enable_execution=row["enable_execution"],
            enable_multi_candidate=row["enable_multi_candidate"],
        ),
        access_control=AccessControl(allowed_users=_names(row["allowed_users"], 256, _USER)),
    )


def load_sealed_catalog(path: Path, targets: dict[int, set[str]]) -> SealedSubagentCatalog:
    raw = read_bounded_bytes(path, _MAX_BYTES)
    data = parse_json(raw)
    if (
        not isinstance(data, dict) or set(data) != {"profile", "subagents"}
        or data["profile"] != "shared-subagents-v1"
        or not isinstance(data["subagents"], list) or not 1 <= len(data["subagents"]) <= 128
    ):
        raise ValueError("Invalid sealed subagent catalog")
    configs = tuple(_config(row, targets) for row in data["subagents"])
    keys = {(config.tenant_id, config.name.casefold()) for config in configs}
    if len(keys) != len(configs):
        raise ValueError("Duplicate subagent identity within a tenant")
    return SealedSubagentCatalog(path, hashlib.sha256(raw).hexdigest(), configs)
