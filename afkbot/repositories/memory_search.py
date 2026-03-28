"""Helper functions for repository-level semantic-memory search fallbacks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite, sqrt


@dataclass(frozen=True, slots=True)
class MemoryRankCandidate:
    """Minimal candidate payload for client-side semantic ranking."""

    item_id: int
    embedding: object
    memory_kind: str
    source_kind: str


def rank_memory_candidates(
    *,
    items: Sequence[MemoryRankCandidate],
    query_embedding: Sequence[float],
    limit: int,
    allowed_memory_kinds: set[str] | None = None,
    allowed_source_kinds: set[str] | None = None,
) -> list[tuple[MemoryRankCandidate, float]]:
    """Score lightweight candidates with cosine distance and return nearest matches."""

    normalized_query = normalize_embedding(query_embedding)
    if normalized_query is None:
        return []
    filtered_items = list(items)
    if allowed_memory_kinds is not None:
        filtered_items = [
            item for item in filtered_items if str(item.memory_kind) in allowed_memory_kinds
        ]
    if allowed_source_kinds is not None:
        filtered_items = [
            item for item in filtered_items if str(item.source_kind) in allowed_source_kinds
        ]

    scored: list[tuple[MemoryRankCandidate, float]] = []
    for item in filtered_items:
        normalized_embedding = normalize_embedding(
            item.embedding,
            expected_dim=len(normalized_query),
        )
        if normalized_embedding is None:
            continue
        scored.append((item, cosine_distance(normalized_query, normalized_embedding)))
    scored.sort(key=lambda pair: pair[1])
    return scored[:limit]


def cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Return cosine distance between two embedding vectors."""

    if not a or len(a) != len(b):
        return 1.0
    dot = sum(value_a * value_b for value_a, value_b in zip(a, b, strict=True))
    norm_a = sqrt(sum(value * value for value in a))
    norm_b = sqrt(sum(value * value for value in b))
    if norm_a == 0 or norm_b == 0:
        return 1.0
    cosine_similarity = dot / (norm_a * norm_b)
    if not isfinite(cosine_similarity):
        return 1.0
    cosine_similarity = max(-1.0, min(1.0, cosine_similarity))
    return 1.0 - cosine_similarity


def normalize_embedding(
    values: object,
    *,
    expected_dim: int | None = None,
) -> tuple[float, ...] | None:
    """Return one finite numeric embedding tuple or None for malformed values."""

    if isinstance(values, (str, bytes, bytearray)):
        return None
    if not isinstance(values, Sequence):
        return None
    try:
        normalized = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return None
    if not normalized:
        return None
    if expected_dim is not None and len(normalized) != expected_dim:
        return None
    if not all(isfinite(value) for value in normalized):
        return None
    return normalized
