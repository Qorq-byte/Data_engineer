"""Request-scoped physical LLM attempt budget; inherited by async child tasks."""

from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any

from app.harness.constraint import ConstraintEnforcer
from app.llm.protocols import CompletionRouter
from app.llm.router import RouterConfig

active_budget: ContextVar[ConstraintEnforcer | None] = ContextVar("llm_budget", default=None)


def reserve_llm_call() -> None:
    budget = active_budget.get()
    if budget is not None:
        if not budget.can_call_llm():
            raise RuntimeError("LLM call budget exhausted")
        budget.record_llm_call()


class BudgetedRouter:
    """Count injected test/adaptor routers; LiteLLM counts each physical attempt itself."""

    def __init__(self, router: CompletionRouter) -> None:
        self._router = router

    @property
    def config(self) -> RouterConfig:
        return self._router.config

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        n: int = 1,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from app.llm.router import LiteLLMRouter

        if getattr(type(self._router), "complete", None) is not LiteLLMRouter.complete:
            reserve_llm_call()
        response = await self._router.complete(
            messages, model=model, temperature=temperature, n=n, **kwargs
        )
        if not isinstance(response, dict):
            raise TypeError("Non-streaming model response must be an object")
        return response

    async def complete_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        from app.llm.router import LiteLLMRouter

        if (
            getattr(type(self._router), "complete_stream", None)
            is not LiteLLMRouter.complete_stream
        ):
            reserve_llm_call()
        async for chunk in self._router.complete_stream(
            messages, model=model, temperature=temperature, **kwargs
        ):
            if not isinstance(chunk, str):
                raise TypeError("Streaming model response chunks must be strings")
            yield chunk
