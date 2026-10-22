"""Node events emitted by the Runner; they describe execution, never route it."""

from collections.abc import Awaitable, Callable
from typing import Literal, NotRequired, TypedDict

from app.nodes.base import NodeOutput

type NodeEventStatus = Literal["started", "completed", "failed"]


class NodeEvent(TypedDict):
    node_id: str
    node: str
    attempt: int
    latency_ms: NotRequired[float]
    output: NotRequired[NodeOutput | None]
    error: NotRequired[str]


type NodeEventHandler = Callable[[NodeEventStatus, NodeEvent], Awaitable[None]]
