"""Structural preflight for offline fixtures; never rewrite labels or SQL semantics."""

from typing import Any

from app.evaluation.predictions import fingerprint


def text(
    value: object,
    limit: int = 1_048_576,
    *,
    empty: bool = False,
    allow_nul: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (not allow_nul and "\x00" in value)
        or (not empty and not value.strip())
        or len(value.encode("utf-8")) > limit
    ):
        raise ValueError("Invalid evaluation text")
    return value


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Evaluation requires objects")
    return value


def _list(value: object, limit: int = 10000, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit or (nonempty and not value):
        raise ValueError("Invalid evaluation list or limit")
    return value


def _strings(value: object, limit: int = 1000) -> list[str]:
    result = [text(item, 512) for item in _list(value, limit)]
    if len(set(result)) != len(result):
        raise ValueError("Duplicate evaluation label")
    return result


def _cases(value: object) -> list[dict[str, Any]]:
    rows = [_object(item) for item in _list(value, nonempty=True)]
    identities = [text(row.get("id"), 512) for row in rows]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate evaluation case id")
    return rows


def _flags(item: dict[str, Any], names: tuple[str, ...]) -> None:
    if any(name in item and type(item[name]) is not bool for name in names):
        raise ValueError("Evaluation flags must be booleans")


def sql_cases(value: object) -> list[dict[str, Any]]:
    rows = _cases(value)
    for row in rows:
        text(row.get("gold_sql"))
        _flags(row, ("ordered",))
    fingerprint(value)
    return rows


def quality_dataset(value: object, k: int) -> dict[str, Any]:
    if type(k) is not int or not 1 <= k <= 1000:
        raise ValueError("Evaluation k must be an integer between 1 and 1000")
    data = _object(value)
    text(data.get("version"), 256)
    documents = [_object(item) for item in _list(data.get("documents"))]
    identities = [text(item.get("id"), 512) for item in documents]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate evaluation document id")
    for item in documents:
        text(item.get("domain"), 512)
        text(item.get("text"), empty=True)
    for item in _cases(data.get("queries")):
        text(item.get("query"))
        text(item.get("domain"), 512)
        _strings(item.get("relevant"))
    if "sql_cases" in data:
        raw = _list(data["sql_cases"])
        if raw:
            sql_cases(raw)
    fingerprint(data)
    return data


def sql_validation_dataset(value: object) -> dict[str, Any]:
    data = _object(value)
    text(data.get("version"), 256)
    text(data.get("scope"))
    for item in _cases(data.get("cases")):
        text(item.get("sql"))
        if type(item.get("expected")) is not bool:
            raise ValueError("SQL fixture expected must be boolean")
        _flags(item, ("policy",))
    fingerprint(data)
    return data


def schema_linking_dataset(value: object) -> dict[str, Any]:
    data = _object(value)
    text(data.get("version"), 256)
    text(data.get("scope"))
    tables = _object(data.get("tables"))
    if len(tables) > 1000:
        raise ValueError("Evaluation table limit exceeded")
    columns, references = 0, 0
    for name, raw in tables.items():
        text(name, 512)
        table = _object(raw)
        columns += len(_strings(table.get("columns")))
        refs = _list(table.get("references"), 1000)
        references += len(refs)
        if columns > 10000 or references > 10000:
            raise ValueError("Evaluation schema size limit exceeded")
        for reference in refs:
            if not isinstance(reference, list) or len(reference) != 3:
                raise ValueError("Invalid evaluation foreign key shape")
            for part in reference:
                text(part, 512)
    allowed_hits = {
        "db_id",
        "table_name",
        "column_name",
        "data_type",
        "comment",
        "is_primary_key",
        "is_foreign_key",
        "embedding_text",
        "dense_rank",
        "sparse_rank",
        "raw",
    }
    for item in _cases(data.get("cases")):
        text(item.get("query"))
        _strings(item.get("expected"))
        _flags(item, ("error", "no_retriever", "fk_expand", "expect_warning"))
        for raw in _list(item.get("hits", []), 1000):
            hit = _object(raw)
            if set(hit) - allowed_hits:
                raise ValueError("Unsupported evaluation hit fields")
            for key in ("db_id", "table_name"):
                text(hit.get(key), 512, empty=True)
            for key in ("column_name", "data_type", "comment"):
                if hit.get(key) is not None:
                    text(hit[key], empty=True)
            if "embedding_text" in hit:
                text(hit["embedding_text"], empty=True)
            _flags(hit, ("is_primary_key", "is_foreign_key"))
            for key in ("dense_rank", "sparse_rank"):
                if hit.get(key) is not None and (type(hit[key]) is not int or hit[key] < 0):
                    raise ValueError("Invalid evaluation hit rank")
            if "raw" in hit:
                _object(hit["raw"])
    fingerprint(data)
    return data


def evaluation_summary(result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        key: result[key]
        for key in (
            "dataset_sha256",
            "query_count",
            "document_count",
            "case_count",
            "passed",
        )
        if key in result
    }
    metrics = result.get("sql_model_metrics")
    if isinstance(metrics, dict):
        summary.update(
            {
                f"sql_{key}": metrics[key]
                for key in (
                    "case_count",
                    "prediction_count",
                    "execution_success_count",
                    "result_correct_count",
                )
            }
        )
    summary["report_sha256"] = fingerprint(result)
    return summary
