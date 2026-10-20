"""Request-scoped physical LLM attempt budget; inherited by async child tasks."""

from contextvars import ContextVar
from typing import Any

from app.harness.constraint import ConstraintEnforcer

active_budget: ContextVar[ConstraintEnforcer | None] = ContextVar("llm_budget", default=None)


def reserve_llm_call() -> None:
    budget = active_budget.get()
    if budget is not None:
        if not budget.can_call_llm():
            raise RuntimeError("LLM call budget exhausted")
        budget.record_llm_call()


class BudgetedRouter:
    """Count injected test/adaptor routers; LiteLLM counts each physical attempt itself."""

    def __init__(self, router: Any):
        self._router = router

    def __getattr__(self, name: str) -> Any:
        return getattr(self._router, name)

    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        from app.llm.router import LiteLLMRouter

        if getattr(type(self._router), "complete", None) is not LiteLLMRouter.complete:
            reserve_llm_call()
        return await self._router.complete(*args, **kwargs)

    async def complete_stream(self, *args: Any, **kwargs: Any) -> Any:
        from app.llm.router import LiteLLMRouter

        if (
            getattr(type(self._router), "complete_stream", None)
            is not LiteLLMRouter.complete_stream
        ):
            reserve_llm_call()
        async for chunk in self._router.complete_stream(*args, **kwargs):
            yield chunk
