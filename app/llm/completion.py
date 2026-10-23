"""Completion status checks without exposing provider text or tool arguments."""

from typing import Any


class IncompleteCompletionError(RuntimeError):
    """Text must not be interpreted as a completed model answer."""


def response_field(value: object, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def check_completion(choice: object) -> None:
    """Accept stop or legacy missing metadata; reject every explicit non-stop reason."""
    reason = response_field(choice, "finish_reason")
    if reason == "length":
        raise IncompleteCompletionError(
            "Model output reached its output limit; shorten the request or review "
            "the configured output token limit before retrying."
        )
    if reason is not None and reason != "stop":
        # Unknown provider values can contain sensitive data. Do not interpolate them.
        raise IncompleteCompletionError("Model response did not finish normally; SQL discarded.")
    for name in ("message", "delta"):
        message = response_field(choice, name)
        if any(response_field(message, field) for field in (
            "refusal", "tool_calls", "function_call",
        )):
            raise IncompleteCompletionError(
                "Model response contains a refusal or tool request; SQL discarded."
            )
