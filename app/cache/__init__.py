"""Query pair cache — two-tier NL→SQL caching.

L1: Exact match via SHA256 hash (in-memory, LRU eviction).
L2: Semantic match via LanceDB vector similarity (cosine distance).
"""

from app.cache.query_cache import CachedQuery, QueryPairCache

__all__ = ["CachedQuery", "QueryPairCache"]
