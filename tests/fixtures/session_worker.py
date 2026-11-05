"""Fresh-process reader/writer for the owned MySQL session fixture."""

import argparse
import asyncio
import json
import re
from pathlib import Path

import aiomysql

from app.storage.shared_sessions import MySQLSessionStore, SessionConflictError


async def run(args):
    pool = await aiomysql.create_pool(
        host="127.0.0.1",
        port=args.port,
        user="root",
        password="stage40-disposable-admin",
        db=args.database,
        autocommit=True,
        minsize=1,
        maxsize=2,
    )
    try:
        store = MySQLSessionStore(pool, args.database, max_entries=2, ttl_seconds=60)
        await store.initialize()
        request = json.loads(Path("input.json").read_text(encoding="utf-8"))
        if args.mode == "write":
            Path("ready").touch()
            async with asyncio.timeout(15):
                while not Path(args.barrier).exists():
                    await asyncio.sleep(0.02)
            try:
                revision = await store.put("shared", request["payload"], request["revision"])
                result = {"committed": True, "revision": revision}
            except SessionConflictError:
                result = {"committed": False}
        else:
            record = await store.get("shared")
            result = {"payload": record.payload, "revision": record.revision} if record else None
        Path("result.pending").write_text(json.dumps(result), encoding="utf-8")
        Path("result.pending").replace("result.json")
    finally:
        pool.close()
        await pool.wait_closed()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("read", "write"))
    parser.add_argument("--database", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--barrier", default="start")
    args = parser.parse_args()
    if not re.fullmatch(r"de_coord66_[0-9a-f]{32}", args.database):
        raise ValueError("Owned fixture database required")
    if not 1024 <= args.port <= 65535 or args.port == 3306:
        raise ValueError("Dedicated test port required")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
