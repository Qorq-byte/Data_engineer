"""Transport-independent request, event and infrastructure-failure contracts."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, Protocol, TypedDict

type QueryStatus = Literal["completed", "failed"]
type QueryEventName = Literal[
    "workflow_start", "stage", "parse", "schema", "sql", "validation", "execution", "error"
]
type QueryEventHandler = Callable[[QueryEventName, dict[str, Any]], Awaitable[None]]
type QueryErrorCode = Literal[
    "QUERY_TIMEOUT", "QUERY_CAPACITY", "INTERNAL_ERROR", "WORKFLOW_FAILED", "INVALID_REQUEST"
]


class QueryInput(Protocol):
    nl_text: str
    session_id: str
    domain: str
    database: str
    dialect: str
    num_candidates: int
    execute: bool
    run_validation: bool
    link_schema: bool
    model: str | None


class QueryErrorPayload(TypedDict):
    code: QueryErrorCode
    message: str
    retry_after_seconds: NotRequired[int]


class QueryCapacityError(RuntimeError):
    """The bounded request execution slots are occupied."""


@dataclass(frozen=True)
class QueryFailure:
    code: QueryErrorCode
    message: str
    http_status: int
    retry_after_seconds: int | None = None

    def payload(self) -> QueryErrorPayload:
        result: QueryErrorPayload = {"code": self.code, "message": self.message}
        if self.retry_after_seconds is not None:
            result["retry_after_seconds"] = self.retry_after_seconds
        return result


def classify_query_failure(exc: Exception) -> QueryFailure:
    """Classify infrastructure failures without exposing raw exception details.

    Business failures stay in the ordinary query response; cancellation is a
    BaseException and must propagate rather than pass through this classifier.
    """
    if isinstance(exc, TimeoutError):
        return QueryFailure("QUERY_TIMEOUT", "Query deadline exceeded", 504)
    if isinstance(exc, QueryCapacityError):
        return QueryFailure(
            "QUERY_CAPACITY", "Query capacity reached; retry explicitly later", 503, 1
        )
    return QueryFailure("INTERNAL_ERROR", "Query failed; inspect server logs", 500)
