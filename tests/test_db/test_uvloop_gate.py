"""The resource gate also supports uvicorn's Linux event-loop implementation."""

import asyncio
import importlib.util

import pytest

from app.db.runtime import discard_gate, gate


def test_gate_on_uvloop_when_available():
    if importlib.util.find_spec("uvloop") is None:
        pytest.skip("uvloop is not supported/installed on this platform")
    import uvloop

    async def exercise():
        semaphore = gate("uvloop-resource")
        assert gate("uvloop-resource") is semaphore
        async with semaphore:
            assert semaphore.locked()
        assert not semaphore.locked()
        discard_gate("uvloop-resource")
        assert gate("uvloop-resource") is not semaphore

    with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
        runner.run(exercise())
