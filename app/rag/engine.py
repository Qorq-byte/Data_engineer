"""RAG engine — module-level singleton holding all RAG components.

Pattern matches :mod:`app.nodes.registry` (module-level singleton mutated at startup).

Usage::

    from app.rag.engine import initialize_rag_engine, get_schema_rag

    await initialize_rag_engine()
    rag = get_schema_rag()
    results = await rag.find_relevant_tables("show top users", top_k=10)
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from app.knowledge.retrieval.bm25_index import BM25Index
from app.knowledge.retrieval.document_store import DocumentStore
from app.knowledge.retrieval.embedding import (
    BGEEmbeddingProvider,
    EmbeddingGenerator,
    MockEmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from app.knowledge.retrieval.lancedb_store import LanceDBStore
from app.knowledge.retrieval.metric_rag import MetricRAG
from app.knowledge.retrieval.rrf_fusion import RRFFusion
from app.knowledge.retrieval.schema_rag import SchemaMetadataRAG
from app.models.rag_settings import FusionWeights

logger = logging.getLogger(__name__)

# ── File paths ────────────────────────────────────────────────────────────

_DEFAULT_LANCEDB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "lancedb"
)
_DEFAULT_BM25_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "bm25.db"
)


# ── RAG Engine container ──────────────────────────────────────────────────


@dataclass
class _RAGEngine:
    """Holds all RAG component instances."""

    lancedb_store: LanceDBStore
    bm25_index: BM25Index
    embedding_generator: EmbeddingGenerator
    schema_rag: SchemaMetadataRAG
    metric_rag: MetricRAG
    document_store: DocumentStore
    index_refresher: Any  # IndexRefresher
    startup_schema_results: dict[str, bool] = field(default_factory=dict)


# ── Module-level singleton ────────────────────────────────────────────────

_engine: _RAGEngine | None = None


def get_rag_engine() -> _RAGEngine:
    """Return the initialized RAG engine singleton.

    Raises:
        RuntimeError: If :func:`initialize_rag_engine` has not been called yet.
    """
    if _engine is None:
        raise RuntimeError("RAG engine not initialized. Call initialize_rag_engine() first.")
    return _engine


def get_schema_rag() -> SchemaMetadataRAG:
    """Return the SchemaMetadataRAG singleton."""
    return get_rag_engine().schema_rag


def get_schema_index_status() -> dict[str, object]:
    """Process state, not a live storage/model health probe; contains operator-only IDs."""
    engine = get_rag_engine()
    required = engine.schema_rag.refresh_required_databases
    unavailable = engine.schema_rag.unavailable_databases
    return {
        "status": "degraded" if required or unavailable else "ready",
        "refresh_required_databases": list(required),
        "recovery_failed_databases": list(unavailable),
        "startup_schema_results": dict(engine.startup_schema_results),
        "scope": "schema indexes only; process state, not a live backend probe",
    }


def get_metric_rag() -> MetricRAG:
    """Return the MetricRAG singleton."""
    return get_rag_engine().metric_rag


def get_document_store() -> DocumentStore:
    """Return the DocumentStore singleton."""
    return get_rag_engine().document_store


def apply_fusion_weights(weights: FusionWeights) -> bool:
    """Publish together on the serving loop; active searches retain their old reference."""
    if _engine is None:
        return False
    fusion = RRFFusion(k=60, alpha=weights.vector_weight)
    _engine.schema_rag.set_fusion(fusion)
    _engine.metric_rag.set_fusion(fusion)
    _engine.document_store.set_fusion(fusion)
    return True


def get_runtime_settings() -> dict[str, Any]:
    """Actual instances, not desired settings or a model/storage health probe."""
    if _engine is None:
        return {"available": False, "weights": None, "embedding": None}
    provider = _engine.embedding_generator.provider
    return {
        "available": True,
        "weights": {
            "schema": FusionWeights(_engine.schema_rag.rrf_alpha).as_dict(),
            "metrics": FusionWeights(_engine.metric_rag.rrf_alpha).as_dict(),
            "documents": FusionWeights(_engine.document_store.rrf_alpha).as_dict(),
        },
        "embedding": {
            "provider": type(provider).__name__,
            "model": provider.model_name,
            "dimension": provider.dimension,
        },
    }


def get_index_refresher() -> Any:
    """Return the IndexRefresher singleton."""
    return get_rag_engine().index_refresher


# ── Initialization ────────────────────────────────────────────────────────


def _create_embedding_provider() -> Any:
    """Pick an embedding provider based on environment and settings.

    Reads ``RAG_EMBEDDING_PROVIDER`` (Settings / env):
        - ``"ollama"`` → OllamaEmbeddingProvider (local, no API key;
          default model ``embeddinggemma`` via ``OLLAMA_EMBEDDING_MODEL``)
        - ``"openai"`` → OpenAIEmbeddingProvider (needs OPENAI_API_KEY)
        - ``"bge"`` → BGEEmbeddingProvider (needs sentence-transformers)
        - ``"mock"`` or unset → MockEmbeddingProvider (deterministic, no deps)
    """
    from app.config.settings import settings

    provider_name = (
        (os.getenv("RAG_EMBEDDING_PROVIDER", "") or settings.rag_embedding_provider).lower().strip()
    )

    if provider_name == "ollama":
        return OllamaEmbeddingProvider(
            model=settings.ollama_embedding_model,
            base_url=settings.ollama_base_url,
            dimension=settings.ollama_embedding_dim,
        )
    elif provider_name == "openai" and os.getenv("OPENAI_API_KEY"):
        return OpenAIEmbeddingProvider()
    elif provider_name == "bge":
        return BGEEmbeddingProvider()
    else:
        return MockEmbeddingProvider(dimension=256)


def _read_rag_settings() -> dict[str, Any]:
    """Read RAG settings from the global settings store, with safe defaults."""
    defaults: dict[str, Any] = {
        "bm25_weight": 0.3,
        "vector_weight": 0.7,
        "top_k": 10,
        "similarity_threshold": 0.6,
        "embedding_model": "text-embedding-3-small",
    }
    try:
        from app.api.settings import _settings_store

        return {**defaults, **_settings_store.get("rag", {})}
    except Exception:
        return defaults


async def initialize_rag_engine() -> _RAGEngine:
    """Create and initialise all RAG components.

    Called from the FastAPI lifespan during server startup; repeated calls are idempotent.
    Must be called inside an async context (running event loop).

    Steps:
        1. Read RAG settings (bm25/vector weights from API settings store).
        2. Pick embedding provider (env-var controlled, mock by default).
        3. Create LanceDBStore + BM25Index persistent stores.
        4. Create EmbeddingGenerator + RRFFusion.
        5. Instantiate SchemaMetadataRAG, MetricRAG, DocumentStore.
        6. Create IndexRefresher wired to the RAG instances.
        7. Index already-connected database schemas.
        8. Index existing glossary terms from loaded domains.
    """
    global _engine
    from app.db.runtime import gate

    async with gate(("rag-engine-initialize",)), gate(("rag-settings",)):
        if _engine is None:
            candidate = await _build_rag_engine()
            _engine = candidate
        return _engine


async def _build_rag_engine() -> _RAGEngine:
    """Build locally; release all acquired handles on failure, timeout or cancellation."""
    rag_settings = _read_rag_settings()
    provider = _create_embedding_provider()
    embedding_gen = EmbeddingGenerator(provider=provider)
    dimension = embedding_gen.dimension

    # ── Persistent stores ──────────────────────────────────────────────
    from app.config.settings import settings

    lancedb_store = LanceDBStore(
        dimension=dimension,
        uri=settings.rag_lancedb_dir or _DEFAULT_LANCEDB_DIR,
        embedding_id=f"{type(provider).__name__}:{provider.model_name}:document-fulltext-v2",
    )
    bm25_index = BM25Index(settings.rag_bm25_path or _DEFAULT_BM25_PATH)

    try:
        # ── RRF fusion with configured weights ─────────────────────────────
        try:
            alpha = FusionWeights(rag_settings.get("vector_weight", 0.7)).vector_weight
        except ValueError:
            logger.warning("Invalid legacy RAG fusion weight; using the safe default")
            alpha = FusionWeights().vector_weight
        rrf = RRFFusion(k=60, alpha=alpha)

        # ── Three RAG applications ─────────────────────────────────────────
        schema_rag = SchemaMetadataRAG(
            lancedb_store=lancedb_store,
            bm25_index=bm25_index,
            embedding_generator=embedding_gen,
            rrf_fusion=rrf,
        )

        metric_rag = MetricRAG(
            lancedb_store=lancedb_store,
            bm25_index=bm25_index,
            embedding_generator=embedding_gen,
            rrf_fusion=rrf,
        )

        document_store = DocumentStore(
            lancedb_store=lancedb_store,
            bm25_index=bm25_index,
            embedding_generator=embedding_gen,
            rrf_fusion=rrf,
        )

        # ── Index refresher ────────────────────────────────────────────────
        from app.rag.index_refresher import IndexRefresher

        index_refresher = IndexRefresher(
            schema_rag=schema_rag,
            metric_rag=metric_rag,
            ttl_minutes=60,
        )

        # ── Assemble engine ────────────────────────────────────────────────
        candidate = _RAGEngine(
            lancedb_store=lancedb_store,
            bm25_index=bm25_index,
            embedding_generator=embedding_gen,
            schema_rag=schema_rag,
            metric_rag=metric_rag,
            document_store=document_store,
            index_refresher=index_refresher,
        )

        # ── Index existing data ────────────────────────────────────────────
        from app.db.connections import ConnectionFactory

        schema_rag.require_refresh(
            lancedb_store.database_ids(schema_rag.NAMESPACE)
            | bm25_index.database_ids(schema_rag.NAMESPACE)
            | {info.db_id for info in ConnectionFactory.list_all()}
        )
        candidate.startup_schema_results = await _index_all_connected_schemas(candidate)
        await _index_all_glossary_terms(candidate)

        return candidate

    except BaseException:
        # Cleanup must not replace the original failure, including CancelledError.
        with contextlib.suppress(Exception):
            bm25_index.close()
        with contextlib.suppress(Exception):
            lancedb_store.close()
        raise


async def _index_all_connected_schemas(engine: _RAGEngine) -> dict[str, bool]:
    """Record each startup outcome; one failed source does not hide healthy sources."""
    from app.db.connections import ConnectionFactory
    from app.db.schema_refresh import refresh_database_schema
    from app.rag.converters import snapshot_to_schema_docs

    results: dict[str, bool] = {}
    for info in ConnectionFactory.list_all():
        try:
            result = await refresh_database_schema(
                info.db_id, engine.schema_rag, document_builder=snapshot_to_schema_docs,
            )
            results[info.db_id] = result.rag_indexed
        except Exception:
            results[info.db_id] = False
        if not results[info.db_id]:
            logger.warning("Startup Schema index refresh failed; source remains unverified")
    return results


async def _index_all_glossary_terms(engine: _RAGEngine) -> None:
    """Index all glossary terms from loaded domain configs into MetricRAG."""
    try:
        from app.api.domains import _glossary_manager
        from app.rag.converters import glossary_term_to_metric_doc

        for domain_name in list(_glossary_manager._domain_terms.keys()):
            try:
                terms = _glossary_manager.list_all(domain=domain_name)
                if terms:
                    docs = [glossary_term_to_metric_doc(t, domain_name) for t in terms]
                    if docs:
                        await engine.metric_rag.index_metrics(docs)
            except Exception:
                pass
    except Exception:
        pass


def close_rag_engine() -> None:
    """Release persistent handles and remove the process singleton at shutdown."""
    global _engine
    engine, _engine = _engine, None
    if engine is not None:
        engine.bm25_index.close()
        engine.lancedb_store.close()
