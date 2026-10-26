"""Binary-relevance ranking metrics shared by offline acceptance profiles."""

import math


def ranking_metrics(retrieved: list[str], relevant: set[str], k: int) -> dict[str, float]:
    hits = [int(doc in relevant) for doc in retrieved[:k]]
    dcg = sum(hit / math.log2(rank + 2) for rank, hit in enumerate(hits))
    ideal = sum(1 / math.log2(rank + 2) for rank in range(min(k, len(relevant))))
    return {
        "recall_at_k": sum(hits) / len(relevant) if relevant else 0,
        "precision_at_k": sum(hits) / k,
        "mrr_at_k": next((1 / (rank + 1) for rank, hit in enumerate(hits) if hit), 0),
        "ndcg_at_k": dcg / ideal if ideal else 0,
    }
