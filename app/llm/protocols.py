"""The narrow router interface required by SQL generation and its budget adapter."""

from collections.abc import AsyncIterator
from typing import Any, Protocol

from app.llm.router import RouterConfig


class CompletionRouter(Protocol):
    @property
    def config(self) -> RouterConfig: ...

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        n: int = 1,
        **kwargs: Any,
    ) -> dict[str, Any]: ...

    def complete_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> AsyncIterator[str]: ...
