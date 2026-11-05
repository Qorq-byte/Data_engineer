"""Process fixture for server-owned MySQL admission locks; no app/model startup."""

import argparse
import asyncio
import json
import re
from pathlib import Path

import aiomysql

from app.harness.mysql_coordination import CoordinationBusyError, MySQLCoordinator


async def run(args):
    pool = await aiomysql.create_pool(host="127.0.0.1", port=args.port, user="root",
                                      password="stage40-disposable-admin", db=args.database,
                                      autocommit=True, minsize=1, maxsize=2)
    try:
        coordinator = MySQLCoordinator(pool, args.database, args.capacity)
        await coordinator.initialize()
        try:
            async with coordinator.query(args.session) as lease:
                Path("result.json").write_text(json.dumps({
                    "entered": True, "slot": lease.slot, "connection_id": lease.connection_id,
                }), encoding="utf-8")
                if args.mode == "hold":
                    async with asyncio.timeout(30):
                        while not Path("release").exists():
                            await asyncio.sleep(0.02)
        except CoordinationBusyError:
            Path("result.json").write_text('{"entered":false}', encoding="utf-8")
    finally:
        pool.close()
        await pool.wait_closed()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("hold", "try"))
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--capacity", type=int, required=True)
    parser.add_argument("--session", default="shared")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port == 3306:
        raise ValueError("Dedicated test service port required")
    if not re.fullmatch(r"de_coord66_[0-9a-f]{32}", args.database):
        raise ValueError("Owned test database required")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
