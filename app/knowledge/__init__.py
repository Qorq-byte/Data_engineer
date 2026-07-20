"""Domain knowledge engine — RAG retrieval, terminology, rules, and evolvable context.

See SPEC §4.2 and implementation-plan.md §4.8 for the full specification.

Subpackages:
    retrieval:  Hybrid search (BM25 + LanceDB + RRF fusion)
"""

from __future__ import annotations
