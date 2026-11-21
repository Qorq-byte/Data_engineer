"""Replay a frozen corpus through the application's document retrieval engine."""

from __future__ import annotations

import tempfile
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.evaluation.acceptance_data import SPLITS, BusinessDataset, fields
from app.evaluation.acceptance_sql import selected

if TYPE_CHECKING:
    from app.knowledge.retrieval.embedding import EmbeddingProvider

PROFILE = "business-document-rankings-v1"


async def capture_document_rankings(
    dataset: BusinessDataset, provider: EmbeddingProvider, *, split: str, k: int, chunk_top_k: int,
) -> dict[str, Any]:
    if split not in SPLITS or not 1 <= k <= chunk_top_k <= 1000:
        raise ValueError("Invalid split or retrieval depth")
    # Saved-output scoring must stay independent of embedding providers and SDKs.
    from app.knowledge.retrieval.bm25_index import BM25Index
    from app.knowledge.retrieval.document_store import DocumentStore
    from app.knowledge.retrieval.embedding import EmbeddingGenerator
    from app.knowledge.retrieval.lancedb_store import LanceDBStore
    from app.models.rag_document import Document

    queries = selected(dataset.retrieval["queries"], split)
    queries_by_scope: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    docs_by_scope: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for query in queries:
        queries_by_scope[query["db_id"], query["domain"]].append(query)
    for doc in dataset.retrieval["documents"]:
        docs_by_scope[doc["db_id"], doc["domain"]].append(doc)

    rankings: dict[str, list[str]] = {}
    for scope, scope_queries in queries_by_scope.items():
        documents = sorted(docs_by_scope[scope], key=lambda row: row["id"])
        if not documents:
            rankings.update({query["id"]: [] for query in scope_queries})
            continue
        with tempfile.TemporaryDirectory(prefix="business-document-rankings-") as tmp:
            vectors = LanceDBStore(provider.dimension, str(Path(tmp) / "vectors"))
            bm25 = BM25Index()
            try:
                store = DocumentStore(vectors, bm25, EmbeddingGenerator(provider))
                # Internal parent IDs cannot collide with DocumentStore's chunk IDs.
                parents = {f"d{index:04d}": doc["id"] for index, doc in enumerate(documents)}
                corpus = [
                    Document(doc_id=parent_id, domain=scope[1], content=doc["text"])
                    for parent_id, doc in zip(parents, documents, strict=True)
                ]
                for start in range(0, len(corpus), 32):
                    await store.index_documents(corpus[start:start + 32])
                for query in scope_queries:
                    hits = await store.search(query["query"], top_k=chunk_top_k)
                    parent_ids = store.get_parent_documents(hits)[:k]
                    rankings[query["id"]] = [parents[parent_id] for parent_id in parent_ids]
            finally:
                bm25.close()
                vectors.close()

    return {
        "profile": PROFILE,
        "manifest_sha256": dataset.hashes["manifest"],
        "split": split,
        "k": k,
        "chunk_top_k": chunk_top_k,
        "embedding_model": provider.model_name,
        "embedding_dimension": provider.dimension,
        "rankings": [{"id": query["id"], "retrieved": rankings[query["id"]]} for query in queries],
    }


def read_rankings(
    value: object, dataset: BusinessDataset | None, split: str, k: int,
) -> tuple[object, dict[str, Any] | None]:
    if not isinstance(value, dict):
        return value, None  # Existing flat ranking files retain their input contract.
    capture = fields(value, "profile manifest_sha256 split k chunk_top_k embedding_model "
                            "embedding_dimension rankings")
    if (
        dataset is None or capture["profile"] != PROFILE
        or capture["manifest_sha256"] != dataset.hashes["manifest"]
        or split not in SPLITS or capture["split"] != split
        or type(capture["k"]) is not int or capture["k"] != k
        or type(capture["chunk_top_k"]) is not int or not k <= capture["chunk_top_k"] <= 1000
        or not isinstance(capture["embedding_model"], str) or not capture["embedding_model"].strip()
        or len(capture["embedding_model"]) > 256
        or type(capture["embedding_dimension"]) is not int
        or not 1 <= capture["embedding_dimension"] <= 65536
    ):
        raise ValueError("Document rankings must match the frozen dataset, split and K")
    rankings = capture["rankings"]
    expected = selected(dataset.retrieval["queries"], split)
    if not isinstance(rankings, list) or len(rankings) != len(expected) or any(
        not isinstance(row, dict) or row.get("id") != query["id"]
        or not isinstance(row.get("retrieved"), list) or len(row["retrieved"]) > k
        for row, query in zip(rankings, expected, strict=True)
    ):
        raise ValueError("Document rankings must cover the selected queries in dataset order")
    return rankings, {key: item for key, item in capture.items() if key != "rankings"}
