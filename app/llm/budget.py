"""Request-scoped physical LLM attempt budget; inherited by async child tasks."""

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextvars import ContextVar
from typing import Any

from app.harness.constraint import ConstraintEnforcer
from app.llm.protocols import CompletionRouter
from app.llm.router import RouterConfig
from app.llm.streaming import StreamEvent, event_method_for, events_for, text_events
from app.llm.usage import active_usage, observe, observe_response

active_budget: ContextVar[ConstraintEnforcer | None] = ContextVar("llm_budget", default=None)
active_call_limits: ContextVar[tuple[ConstraintEnforcer, ...]] = ContextVar(
    "llm_caller_limits", default=(),
)
# Optional caller policy, evaluated before reservation and transport. No global policy change.
active_call_guard: ContextVar[Callable[..., None] | None] = ContextVar(
    "llm_caller_guard", default=None,
)


def reserve_llm_call() -> None:
    query = active_budget.get()
    budgets = list(dict.fromkeys(
        (() if query is None else (query,)) + active_call_limits.get()
    ))
    # No await between checking all scopes and reserving: rejection consumes none.
    if any(not budget.can_call_llm() for budget in budgets):
        raise RuntimeError("LLM call budget exhausted")
    for budget in budgets:
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

        native = getattr(type(self._router), "complete", None) is LiteLLMRouter.complete
        if not native:
            reserve_llm_call()
        with observe(
            None if native else active_usage.get(),
            "adapter",
            model
            or str(
                getattr(getattr(self._router, "config", None), "default_model", None) or "unknown"
            ),
            "adapter_boundary",
        ):
            response = await self._router.complete(
                messages, model=model, temperature=temperature, n=n, **kwargs
            )
            if not native:
                observe_response(response)
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

        native = (
            getattr(self._router.complete_stream, "__func__", None)
            is LiteLLMRouter.complete_stream
            and getattr(getattr(self._router, "complete_stream_events", None), "__func__", None)
            is LiteLLMRouter.complete_stream_events
        )
        if not native:
            reserve_llm_call()
        stream = self._router.complete_stream(
            messages, model=model, temperature=temperature, **kwargs
        )
        with observe(
            None if native else active_usage.get(),
            "adapter",
            model
            or str(
                getattr(getattr(self._router, "config", None), "default_model", None) or "unknown"
            ),
            "adapter_boundary",
        ):
            try:
                async for chunk in stream:
                    if not isinstance(chunk, str):
                        raise TypeError("Streaming model response chunks must be strings")
                    yield chunk
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()

    async def complete_stream_events(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        from app.llm.router import LiteLLMRouter

        method = event_method_for(self._router, "complete_stream", "complete_stream_events")
        if method is None:
            stream = text_events(self.complete_stream(
                messages, model=model, temperature=temperature, **kwargs,
            ))
            try:
                async for event in stream:
                    yield event
            finally:
                await stream.aclose()
            return
        native = (
            getattr(method, "__func__", None) is LiteLLMRouter.complete_stream_events
        )
        if not native:
            reserve_llm_call()
        with observe(
            None if native else active_usage.get(), "adapter",
            model or self.config.default_model, "adapter_boundary",
        ):
            stream = events_for(self._router, "complete_stream", "complete_stream_events", messages,
                                model=model, temperature=temperature, **kwargs)
            try:
                async for event in stream:
                    if not native and event.kind == "end" and event.finish_reason == "stop":
                        observe_response({"choices": [{"finish_reason": "stop"}]})
                    yield event
            finally:
                await stream.aclose()
