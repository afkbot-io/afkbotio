"""Small text-formatting helpers shared by chat-session and workspace modules."""

from __future__ import annotations


def truncate_compact_text(value: str, *, max_length: int) -> str:
    """Normalize whitespace and truncate one value with an ellipsis when needed."""

    text = " ".join(value.split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."
