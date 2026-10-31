"""Real retrieval backends over verified exact-text vectors, with raw-result checks."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from app.evaluation.acceptance_retrieval import evaluate_retrieval
from app.evaluation.artifacts import write_atomic
from app.evaluation.embedding_batch import DIMENSION, load_inputs, manifest_digest, text_sha
from app.evaluation.embedding_probe import QUERY_TEXT, schema_prompt_probe
from app.evaluation.embedding_runtime import MeasurementError, validate_vectors
from app.evaluation.predictions import fingerprint
from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.embedding import EmbeddingGenerator, EmbeddingProvider, EmbeddingResult
from app.knowledge.retrieval.filters import literal
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.rrf_fusion import RRFFusion
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.models.rag_schema import SchemaDocument


class ReplayProvider(EmbeddingProvider):
    """No client or fallback: only the validated 53 exact inputs can be replayed."""

    def __init__(self, artifact: object, manifest: dict[str, Any]):
        self.vectors = validate_vectors(artifact, manifest)
        _, inputs = load_inputs()
        self.texts = {row["text"] for row in inputs}

    @property
    def dimension(self) -> int:
        return DIMENSION

    @property
    def model_name(self) -> str:
        return "verified-exact-text-replay"

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if any(text not in self.texts for text in texts):
            raise MeasurementError("UNAPPROVED_REPLAY_INPUT")
        return EmbeddingResult([list(self.vectors[text_sha(text)]) for text in texts],
                               self.model_name, DIMENSION)


def table_name(identity: str) -> str:
    suffix = identity.split(".")[-1]
    if suffix in {"users", "email", "names"}:
        return "users"
    if suffix in {"products", "category", "stock", "price"}:
        return "products"
    if suffix in {"payroll", "trace"}:
        return suffix
    return "orders"


def validate_rows(rows: list[dict[str, Any]], documents: dict[str, dict[str, Any]],
                  db_id: str, domain: str, limit: int, *, dense: bool) -> None:
    eligible = {key for key, doc in documents.items()
                if doc["db_id"] == db_id and doc["domain"] == domain}
    ids = [row.get("doc_id") for row in rows]
    if (len(ids) != len(set(ids)) or any(identity not in eligible for identity in ids)
            or len(rows) > min(limit, len(eligible))
            or (dense and len(rows) != min(limit, len(eligible)))):
        raise MeasurementError("INVALID_RAW_RETRIEVAL_IDS")
    for row in rows:
        meta = row if dense else row.get("metadata", {})
        if (meta.get("db_id") != db_id or meta.get("schema_name") != "main"
                or meta.get("table_name") != table_name(row["doc_id"])
                or (not dense and meta.get("benchmark_domain") != domain)):
            raise MeasurementError("INVALID_RAW_RETRIEVAL_METADATA")


class ScopedDense(LanceDBStore):
    scope: tuple[str, str] | None = None

    def __init__(self, directory: Path, identity: str, documents: dict[str, dict[str, Any]]):
        super().__init__(DIMENSION, str(directory), embedding_id=identity)
        self.documents = documents

    def search(self, namespace: str, vector: list[float], top_k: int = 10, *,
               filter_expr: str | None = None) -> list[dict[str, Any]]:
        if self.scope is None:
            raise MeasurementError("RETRIEVAL_SCOPE_REQUIRED")
        db_id, domain = self.scope
        ids = [key for key, doc in self.documents.items()
               if doc["db_id"] == db_id and doc["domain"] == domain]
        if not ids:
            return []
        restriction = "doc_id IN (" + ", ".join(literal(key) for key in ids) + ")"
        restriction += f" AND db_id = {literal(db_id)}"
        if filter_expr:
            restriction += f" AND ({filter_expr})"
        rows = super().search(namespace, vector, top_k, filter_expr=restriction)
        validate_rows(rows, self.documents, db_id, domain, top_k, dense=True)
        return rows


class ScopedSparse(BM25Index):
    scope: tuple[str, str] | None = None

    def __init__(self, directory: Path, documents: dict[str, dict[str, Any]]):
        super().__init__(str(directory))
        self.documents = documents

    def index(self, namespace: str, docs: list[dict[str, Any]], language: str = "auto") -> int:
        enriched = [{**doc, "metadata": {**doc.get("metadata", {}),
                    "benchmark_domain": self.documents[doc["doc_id"]]["domain"]}} for doc in docs]
        return super().index(namespace, enriched, language)

    def search(self, namespace: str, query: str, top_k: int = 10, language: str = "auto",
               metadata_filter: dict[str, str] | None = None) -> list[dict[str, Any]]:
        if self.scope is None:
            raise MeasurementError("RETRIEVAL_SCOPE_REQUIRED")
        db_id, domain = self.scope
        filters = dict(metadata_filter or {})
        if "db_id" in filters and filters["db_id"] != db_id:
            raise MeasurementError("RETRIEVAL_SCOPE_MISMATCH")
        filters.update(db_id=db_id, benchmark_domain=domain)
        rows = super().search(namespace, query, top_k, language, filters)
        validate_rows(rows, self.documents, db_id, domain, top_k, dense=False)
        return rows


def latency(samples: list[float], failures: int) -> dict[str, Any]:
    ordered = sorted(samples)
    def percentile(p: float) -> float | None:
        return ordered[max(0, math.ceil(p * len(ordered)) - 1)] if ordered else None
    return {"attempts": len(samples), "failures": failures,
            "p50_ms": percentile(.5), "p95_ms": percentile(.95), "p99_ms": percentile(.99),
            "samples_ms": samples, "policy": "nearest rank; failed attempt time included"}


async def measure_rankings(manifest: dict[str, Any], artifact: dict[str, Any],
                           directory: Path, suite: dict[str, Any]) -> dict[str, Any]:
    provider = ReplayProvider(artifact, manifest)
    frozen, _ = load_inputs()
    if fingerprint(suite) != fingerprint(frozen):
        raise MeasurementError("RETRIEVAL_SUITE_IDENTITY_MISMATCH")
    documents = {doc["id"]: doc for doc in suite["documents"]}
    identity = fingerprint({"manifest": manifest_digest(manifest),
                            "vectors": artifact["artifact_sha256"], "model": manifest["model"]})
    index_dir = directory / "index"
    index_dir.mkdir(exist_ok=False)  # Never touch or repair a previous run's index.
    dense = ScopedDense(index_dir / "lance", identity, documents)
    sparse = ScopedSparse(index_dir / "sparse.sqlite", documents)
    result: dict[str, Any] = {"index_ready": False, "technical_errors": [], "modes": {},
                              "index_identity": identity, "latency": {},
                              "vector_source": "verified exact-text replay; no embedding HTTP",
                              "bm25_scope": "Shared enriched metadata representation with hybrid; "
                              "FTS5 also indexes metadata, so not comparable to stage42 scores"}
    try:
        started = time.perf_counter()
        rag = SchemaMetadataRAG(dense, sparse, EmbeddingGenerator(provider),
                                RRFFusion(k=60, alpha=.7))
        await rag.index_schemas([SchemaDocument(doc_id=doc["id"], db_id=doc["db_id"],
            schema_name="main", table_name=table_name(doc["id"]), embedding_text=doc["text"])
            for doc in suite["documents"]])
        namespace = rag.NAMESPACE
        for store in (dense, sparse):
            indexed_ids: list[str] = []
            for db_id in {doc["db_id"] for doc in documents.values()}:
                if isinstance(store, ScopedDense):
                    indexed_ids.extend(row["doc_id"] for row in
                                       store.snapshot_database(namespace, db_id))
                else:
                    indexed_ids.extend(row[0] for row in
                                       store.snapshot_database(namespace, db_id))
            if (store.count(namespace) != 23 or len(indexed_ids) != 23
                    or set(indexed_ids) != set(documents)):
                raise MeasurementError("INDEX_CONTENT_MISMATCH")
        result.update(index_ready=True, index_counts=rag.stats(),
                      index_build_ms=(time.perf_counter() - started) * 1000)
        write_atomic(index_dir / "identity.json", {"identity": identity, "dimension": DIMENSION,
                                                   "model": manifest["model"]})
        for mode in ("bm25", "dense", "hybrid"):
            predictions = []
            samples = []
            failures = 0
            for repetition in range(3):
                for query in suite["queries"]:
                    dense.scope = sparse.scope = (query["db_id"], query["domain"])
                    started = time.perf_counter()
                    try:
                        if mode == "bm25":
                            rows = sparse.search(namespace, query["query"], 3)
                            ranking = [row["doc_id"] for row in rows]
                        elif mode == "dense":
                            vector = (await provider.embed([query["query"]])).vectors[0]
                            rows = dense.search(namespace, vector, 3)
                            ranking = [row["doc_id"] for row in rows]
                        else:
                            hits = await rag.find_relevant_tables(query["query"], 3,
                                                                  db_id=query["db_id"])
                            ranking = [hit.doc_id for hit in hits]
                        if repetition == 0:
                            predictions.append({"id": query["id"], "retrieved": ranking})
                    except Exception as exc:
                        failures += 1
                        result["technical_errors"].append({"mode": mode, "id": query["id"],
                            "repetition": repetition, "error": type(exc).__name__,
                            "code": getattr(exc, "code", "RETRIEVAL_FAILED")})
                    finally:
                        samples.append((time.perf_counter() - started) * 1000)
            result["modes"][mode] = evaluate_retrieval(suite, predictions, k=3)
            result["latency"][mode] = latency(samples, failures)
        dense.scope = sparse.scope = ("shop_a", "sales")
        result["probe"] = await schema_prompt_probe(rag, QUERY_TEXT, directory)
    except Exception as exc:
        result["technical_errors"].append({"error": type(exc).__name__,
            "code": getattr(exc, "code", "INDEX_OR_PROBE_FAILED")})
    finally:
        sparse.close()
        dense.close()
        write_atomic(directory / "measurement.json", result)
    return result
