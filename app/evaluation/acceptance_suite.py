"""Self-check frozen acceptance labels before saved-output scoring."""

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


def self_check(dataset: data.BusinessDataset | None = None) -> dict[str, Any]:
    if dataset is None:
        manifest = data.verify_manifest()
        sql, retrieval, snapshot = load_sql(), load_retrieval(), None
        profile = "synthetic-acceptance-v3"
        manifest_hash, input_hashes = fingerprint(manifest), manifest["files"]
        raw_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in data.source_paths()}
        limitations = ("Manual public synthetic dev/holdout consistency, not model accuracy. "
                       "No private business labels; public holdout is not blind.")
    else:
        sql, retrieval, snapshot = dataset.sql, dataset.retrieval, dataset.snapshot
        profile = data.BUSINESS_PROFILE
        manifest_hash = dataset.hashes["manifest"]
        input_hashes = {name: dataset.hashes[name] for name in data.BUSINESS_FILES}
        raw_hashes = {"manifest": manifest_hash, **input_hashes}
        limitations = ("Manual business labels checked against one frozen SQLite snapshot. "
                       "This label check contains no model predictions.")
    return {"profile": profile, "report_version": "acceptance-self-check-v1",
            "manifest_sha256": manifest_hash,
            "input_sha256": input_hashes,
            "raw_input_sha256": raw_hashes,
            "sql_dataset_sha256": fingerprint(sql),
            "retrieval_dataset_sha256": fingerprint(retrieval),
            "sql_statistics": _statistics(sql["sql_cases"]),
            "retrieval_statistics": _statistics(retrieval["queries"]),
            "document_count": len(retrieval["documents"]),
            "sql": sql_self_check(sql, snapshot=snapshot),
            "retrieval": retrieval_self_check(retrieval, business=dataset is not None),
            "llm_calls": 0, "embedding_calls": 0, "actual_cost_usd": None,
            "limitations": f"{limitations} Checksums detect drift, not authenticity or "
                           "independent human review."}
