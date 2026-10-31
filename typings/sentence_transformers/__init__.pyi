"""Partial sentence-transformers surface used by this repository; no runtime implementation.

Only the surface reached from ``app.knowledge.retrieval.embedding.BGEEmbeddingProvider``
is declared: constructing a ``SentenceTransformer`` from a model name and calling
``encode`` on a list of texts with ``normalize_embeddings``.

``encode`` returns a numpy array or a torch tensor depending on the loaded backend,
so its return value stays ``Any``; callers already coerce each vector element to
float. The optional dependency is not installed in this environment, so this file
is a declaration for mypy only and must be rechecked before adding new calls.
"""

from typing import Any

class SentenceTransformer:
    def __init__(self, model_name_or_path: str, **kwargs: Any) -> None: ...
    def encode(self, sentences: list[str], **kwargs: Any) -> Any: ...
