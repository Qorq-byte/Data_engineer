"""Small immutable value object shared by configuration and retrieval."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FusionWeights:
    vector_weight: float = 0.7

    def __post_init__(self) -> None:
        value = self.vector_weight
        if (
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError("vector_weight must be a finite number between 0 and 1")

    def as_dict(self) -> dict[str, float]:
        return {
            "bm25_weight": round(1 - self.vector_weight, 12),
            "vector_weight": float(self.vector_weight),
        }
