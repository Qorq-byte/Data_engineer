"""Database initialization script — creates the MySQL schema and seeds defaults.

Usage::

    python scripts/init_db.py                 # Uses default config (env vars)
    python scripts/init_db.py --host 127.0.0.1 --user root --password mypass
    python scripts/init_db.py --drop          # Drop and recreate all tables

Environment variables:
    MYSQL_HOST      — MySQL host (default: 127.0.0.1)
    MYSQL_PORT      — MySQL port (default: 3306)
    MYSQL_USER      — MySQL user (default: root)
    MYSQL_PASSWORD  — MySQL password
    MYSQL_DATABASE  — Database name (default: nl2sql_engine)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def _init(drop: bool = False, config: dict | None = None) -> None:
    """Initialize the database."""
    from app.storage.mysql_store import DEFAULT_MYSQL_CONFIG, MySQLStore, init_db

    if drop:
        cfg = {**DEFAULT_MYSQL_CONFIG, **(config or {})}
        store = await MySQLStore.create(cfg)
        for table in [
            "learning_query_pairs",
            "learning_quality_trends",
            "learning_rule_candidates",
            "feedback",
            "query_history",
            "domain_rules",
            "glossary_terms",
            "domains",
            "datasource_connections",
            "api_keys",
            "user_settings",
        ]:
            try:
                await store._execute(f"DROP TABLE IF EXISTS `{table}`")
                print(f"  DROPPED: {table}")
            except Exception as e:
                print(f"  SKIP {table}: {e}")
        await store.close()
        print("All tables dropped. Re-initializing...")

    store = await init_db(config)
    print(f"Database initialized: {DEFAULT_MYSQL_CONFIG['db']}")
    print(f"Tables created: 11")

    # Verify
    settings = await store.get_settings()
    print(f"Settings sections: {', '.join(settings.keys())}")

    await store.close()
    print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize NL2SQL MySQL database")
    parser.add_argument("--host", default=None, help="MySQL host")
    parser.add_argument("--port", type=int, default=None, help="MySQL port")
    parser.add_argument("--user", default=None, help="MySQL user")
    parser.add_argument("--password", default=None, help="MySQL password")
    parser.add_argument("--database", default=None, help="Database name")
    parser.add_argument("--drop", action="store_true", help="Drop all tables first")
    args = parser.parse_args()

    config = {}
    if args.host:
        config["host"] = args.host
    if args.port:
        config["port"] = args.port
    if args.user:
        config["user"] = args.user
    if args.password:
        config["password"] = args.password
    if args.database:
        config["db"] = args.database

    asyncio.run(_init(drop=args.drop, config=config or None))


if __name__ == "__main__":
    main()