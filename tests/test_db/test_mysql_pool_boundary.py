"""Exercise the real driver's pool context lifecycle without opening a socket."""

import aiomysql


async def test_empty_mysql_pool_context_closes_without_connecting():
    # minsize=0 defers all connections until acquire(); no MySQL service is needed.
    async with aiomysql.create_pool(minsize=0, maxsize=1) as pool:
        assert not pool.closed
        assert pool.size == 0
    assert pool.closed
