"""Result wire format and synchronous driver surface used by execution helpers.

SQL cell values remain driver dependent (decimal, bytes, temporal values, etc.).
These protocols describe only the methods consumed here, not a complete driver.
"""

from collections.abc import Sequence
from typing import Any, Protocol, TypedDict


class ExecutionResult(TypedDict):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool


class ReadCursor(Protocol):
    @property
    def description(self) -> Sequence[Sequence[Any]] | None: ...

    def execute(self, sql: str, /) -> object: ...

    def fetchmany(self, size: int, /) -> Sequence[Sequence[Any]]: ...

    def fetchall(self) -> Sequence[Sequence[Any]]: ...


class CursorConnection(Protocol):
    def cursor(self) -> ReadCursor: ...


class DuckReadConnection(Protocol):
    def execute(self, sql: str, /) -> ReadCursor: ...
