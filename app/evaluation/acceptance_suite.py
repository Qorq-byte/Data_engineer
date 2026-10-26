"""Frozen synthetic acceptance profile; self-check labels before saved-output scoring."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from app.evaluation import acceptance_data as data
from app.evaluation.acceptance_retrieval import load_retrieval, retrieval_self_check
from app.evaluation.acceptance_sql import load_sql, sql_self_check
from app.evaluation.predictions import fingerprint


def _statistics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"case_count": len(rows), "split_counts": dict(Counter(row["split"] for row in rows)),
            "tags": dict(Counter(tag for row in rows for tag in row["tags"])),
            "by_split": {split: dict(Counter(tag for row in rows if row["split"] == split
                                            for tag in row["tags"])) for split in data.SPLITS}}


def self_check() -> dict[str, Any]:
    manifest = data.verify_manifest()
    sql = load_sql()
    retrieval = load_retrieval()
    return {"profile": "synthetic-acceptance-v3", "report_version": "acceptance-self-check-v1",
            "manifest_sha256": fingerprint(manifest),
            "input_sha256": manifest["files"],
            "raw_input_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in data.source_paths()},
            "sql_dataset_sha256": fingerprint(sql),
            "retrieval_dataset_sha256": fingerprint(retrieval),
            "sql_statistics": _statistics(sql["sql_cases"]),
            "retrieval_statistics": _statistics(retrieval["queries"]),
            "document_count": len(retrieval["documents"]),
            "sql": sql_self_check(sql), "retrieval": retrieval_self_check(retrieval),
            "llm_calls": 0, "embedding_calls": 0, "actual_cost_usd": None,
            "limitations": "Manual public synthetic dev/holdout consistency, not model accuracy. "
                           "No private business labels; public holdout is not blind. "
                           "Checksums detect drift, not authenticity or independent human review."}
