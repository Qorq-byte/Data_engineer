"""Optional, invocation-local stream evidence with conservative text compatibility."""

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Literal, cast

from app.llm.completion import IncompleteCompletionError


@dataclass(frozen=True)
class StreamEvent:
    kind: Literal["delta", "end"]
    text: str = ""
    finish_reason: Literal["stop"] | None = None
    generation_source: Literal["model_stream", "stream_text"] = "stream_text"


def event_method_for(
    target: object, text_name: str, event_name: str,
) -> Callable[..., AsyncIterator[StreamEvent]] | None:
    """An inherited capability must not bypass a more recent text override."""
    method = getattr(target, event_name, None)
    if not callable(method):
        return None
    instance = getattr(target, "__dict__", {})
    if text_name in instance and event_name not in instance:
        return None
    owners = type(target).__mro__
    text_owner = next((i for i, cls in enumerate(owners) if text_name in vars(cls)), len(owners))
    event_owner = next((i for i, cls in enumerate(owners) if event_name in vars(cls)), len(owners))
    if event_name not in instance and text_owner < event_owner:
        return None
    return cast(Callable[..., AsyncIterator[StreamEvent]], method)


async def text_events(stream: AsyncIterator[str]) -> AsyncGenerator[StreamEvent, None]:
    try:
        async for text in stream:
            if not isinstance(text, str):
                raise IncompleteCompletionError("Model text stream contained non-text output.")
            yield StreamEvent("delta", text=text)
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
    yield StreamEvent("end")


async def checked_events(stream: AsyncIterator[StreamEvent]) -> AsyncGenerator[StreamEvent, None]:
    """Hold terminal evidence until EOF, rejecting any continuation or malformed event."""
    terminal = None
    try:
        async for event in stream:
            if not isinstance(event, StreamEvent):
                raise IncompleteCompletionError("Invalid model stream event.")
            if terminal is not None:
                raise IncompleteCompletionError(
                    "Model stream continued after its completion event."
                )
            if (
                not isinstance(event.text, str)
                or event.generation_source not in ("model_stream", "stream_text")
            ):
                raise IncompleteCompletionError("Invalid model stream event fields.")
            if event.kind == "delta" and event.finish_reason is None:
                yield event
            elif event.kind == "end" and not event.text and (
                (event.generation_source == "model_stream" and event.finish_reason == "stop")
                or (event.generation_source == "stream_text" and event.finish_reason is None)
            ):
                terminal = event
            else:
                raise IncompleteCompletionError("Invalid model stream completion event.")
        if terminal is None:
            raise IncompleteCompletionError("Model stream ended without a completion event.")
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
    yield terminal


async def events_for(
    target: object, text_name: str, event_name: str, *args: Any, **kwargs: Any,
) -> AsyncGenerator[StreamEvent, None]:
    method = event_method_for(target, text_name, event_name)
    source = (
        method(*args, **kwargs) if method is not None
        else text_events(getattr(target, text_name)(*args, **kwargs))
    )
    stream = checked_events(source)
    try:
        async for event in stream:
            yield event
    finally:
        await stream.aclose()
