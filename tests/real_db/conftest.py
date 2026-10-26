import os

import pytest
import pytest_asyncio

from app.db.connections import ConnectionFactory, DatabaseType
from tests.real_db.support import configured_service, initialize


@pytest_asyncio.fixture(scope="session", loop_scope="session", params=["postgresql", "mysql"])
async def service(request, record_testsuite_property):
    if os.environ.get("DE_REAL_DB_TESTS") != "1":
        pytest.skip("Disposable database services are opt-in")
    selected = configured_service(request.param)
    version = await initialize(selected)
    record_testsuite_property(f"server_{selected.kind}", version)
    return selected


@pytest_asyncio.fixture
async def connection(service):
    identity = f"stage40-{service.kind}"
    conn = await ConnectionFactory.create(DatabaseType(service.kind), service.config(), identity)
    try:
        yield identity, conn
    finally:
        await ConnectionFactory.aclose(identity)
