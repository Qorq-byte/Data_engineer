"""Lance SQL string literals; BM25 uses bound parameters."""


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
