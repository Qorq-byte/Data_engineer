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
    "QUERY_TIMEOUT",
    "QUERY_CAPACITY",
    "INTERNAL_ERROR",
    "WORKFLOW_FAILED",
    "INVALID_REQUEST",
    "SESSION_CONFLICT",
    "SHARED_STATE_UNAVAILABLE",
    "DOMAIN_CONFIG_UNAVAILABLE",
    "LLM_CONFIG_UNAVAILABLE",
    "LLM_MODEL_UNAVAILABLE",
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
    plan_id: str
    auto_plan: bool


class QueryErrorPayload(TypedDict):
    code: QueryErrorCode
    message: str
    retry_after_seconds: NotRequired[int]


class QueryCapacityError(RuntimeError):
    """The bounded request execution slots are occupied."""


class QueryPlanError(ValueError):
    """The requested workflow plan cannot run for this request.

    Raised before a session or LLM call is created so a bad ``plan_id`` is a
    client error rather than a partially executed workflow.
    """


class QueryDomainUnavailableError(RuntimeError):
    """The current tenant's domain configuration could not be read safely."""


class QueryTenantLLMUnavailableError(RuntimeError):
    """The current tenant has no usable persisted model configuration."""


class QueryModelUnavailableError(ValueError):
    """The requested model is unavailable in the tenant's configuration."""


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
    from app.harness.mysql_coordination import CoordinationUnavailableError
    from app.storage.shared_sessions import (
        SessionCapacityError,
        SessionConflictError,
        SessionStoreUnavailableError,
    )

    if isinstance(exc, SessionConflictError):
        return QueryFailure("SESSION_CONFLICT", "Session changed; read before retrying", 409)
    if isinstance(exc, CoordinationUnavailableError | SessionStoreUnavailableError):
        return QueryFailure("SHARED_STATE_UNAVAILABLE", "Shared state unavailable", 503, 1)
    if isinstance(exc, QueryDomainUnavailableError):
        return QueryFailure("DOMAIN_CONFIG_UNAVAILABLE", "Domain configuration unavailable", 503, 1)
    if isinstance(exc, QueryTenantLLMUnavailableError):
        return QueryFailure("LLM_CONFIG_UNAVAILABLE", "Tenant model configuration unavailable", 503)
    if isinstance(exc, QueryModelUnavailableError):
        return QueryFailure(
            "LLM_MODEL_UNAVAILABLE", "Requested model unavailable for this tenant", 422,
        )
    if isinstance(exc, SessionCapacityError):
        return QueryFailure("QUERY_CAPACITY", "Shared session capacity reached", 503, 1)
    if isinstance(exc, TimeoutError):
        return QueryFailure("QUERY_TIMEOUT", "Query deadline exceeded", 504)
    if isinstance(exc, QueryCapacityError):
        return QueryFailure(
            "QUERY_CAPACITY", "Query capacity reached; retry explicitly later", 503, 1
        )
    if isinstance(exc, QueryPlanError):
        return QueryFailure("INVALID_REQUEST", str(exc), 400)
    return QueryFailure("INTERNAL_ERROR", "Query failed; inspect server logs", 500)
