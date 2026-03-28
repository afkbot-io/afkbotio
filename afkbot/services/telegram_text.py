"""Shared Telegram text limits and chunking helpers."""

from __future__ import annotations

TELEGRAM_TEXT_LIMIT = 4096


def split_telegram_text(
    text: str,
    *,
    max_chars: int = TELEGRAM_TEXT_LIMIT,
) -> tuple[str, ...]:
    """Split one long Telegram text into delivery-safe chunks."""

    normalized = text.strip()
    if not normalized:
        return ()
    if len(normalized) <= max_chars:
        return (normalized,)
    chunks: list[str] = []
    remaining = normalized
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        split_at = _find_split_index(remaining, max_chars=max_chars)
        chunk = remaining[:split_at].rstrip()
        if not chunk:
            split_at = max_chars
            chunk = remaining[:split_at]
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()
    return tuple(chunks)


def _find_split_index(text: str, *, max_chars: int) -> int:
    minimum_preferred_index = max(max_chars // 2, 1)
    for separator in ("\n\n", "\n", " "):
        index = text.rfind(separator, 0, max_chars + 1)
        if index >= minimum_preferred_index:
            return index + len(separator)
    return max_chars
