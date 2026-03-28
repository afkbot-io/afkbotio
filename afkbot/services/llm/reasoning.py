"""Shared reasoning-level contracts for LLM planning flows."""

from __future__ import annotations

from typing import Literal, get_args

ThinkingLevel = Literal["low", "medium", "high", "very_high"]
ReasoningEffort = Literal["low", "medium", "high"]


def normalize_thinking_level(value: str | None) -> ThinkingLevel | None:
    """Normalize user-facing thinking level aliases into canonical runtime values."""

    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_")
    if not normalized:
        return None
    allowed = set(get_args(ThinkingLevel))
    if normalized not in allowed:
        raise ValueError(
            "thinking_level must be one of: low, medium, high, very_high",
        )
    return normalized  # type: ignore[return-value]
