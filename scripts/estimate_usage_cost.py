"""Estimate observed request tokens using explicit operator flat prices; no live pricing or LLM."""

import argparse
import json
import sqlite3
from pathlib import Path

from app.audit.store import UsageAuditStore
from app.evaluation.artifacts import protect_inputs, read_json, write_atomic
from app.evaluation.usage_costs import estimate_usage_costs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-db", type=Path, required=True)
    parser.add_argument("--query-id", action="append", required=True)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if not 1 <= len(args.query_id) <= 100 or len(set(args.query_id)) != len(args.query_id):
            raise ValueError("Invalid query selection")
        protected = [args.prices, args.audit_db]
        protected.extend(
            Path(str(args.audit_db) + suffix) for suffix in ("-wal", "-shm", "-journal")
        )
        protect_inputs(args.output, protected)
        store = UsageAuditStore(str(args.audit_db))
        records = []
        for query_id in args.query_id:
            record = store.read(query_id)
            if record is None:
                raise ValueError("Requested audit record unavailable")
            records.append(record)
        report = estimate_usage_costs(records, read_json(args.prices))
        write_atomic(args.output, report, prefix=".usage-cost-estimate-")
    except (OSError, ValueError, TypeError, sqlite3.Error):
        parser.exit(2, "Usage cost estimate failed; check audit records, prices and file access.\n")
    print(
        json.dumps(
            {
                "query_count": report["summary"]["query_count"],
                "report_sha256": report["report_sha256"],
            }
        )
    )


if __name__ == "__main__":
    main()
