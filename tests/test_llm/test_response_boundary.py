"""Reject a wrong completion response shape before it becomes SQL input."""

import pytest

from app.llm.router import LiteLLMRouter, RouterConfig


@pytest.mark.parametrize("response", [None, [], "unstructured text", 123])
async def test_nonstream_completion_rejects_nonobject_response(monkeypatch, response):
    router = LiteLLMRouter(RouterConfig(), mock_mode=True)

    async def invalid_response(*args, **kwargs):
        return response

    monkeypatch.setattr(router, "_route_with_fallback", invalid_response)
    with pytest.raises(TypeError, match="Non-streaming model response must be an object"):
        await router.complete([{"role": "user", "content": "query"}])
