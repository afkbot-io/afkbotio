"""Shared helpers for collision-safe, DB-bounded session identifiers."""

from __future__ import annotations

from hashlib import sha256
from urllib.parse import quote

MAX_SESSION_ID_LENGTH = 64
_HASH_HEX_LENGTH = 12


def encode_session_component(value: str) -> str:
    """Encode one raw selector fragment for delimiter-safe session composition."""

    return quote(value, safe="")


def compose_bounded_session_id(*parts: str, max_length: int = MAX_SESSION_ID_LENGTH) -> str:
    """Join non-empty parts and suffix with a stable hash when length would overflow."""

    normalized = [part.strip() for part in parts if part.strip()]
    raw = ":".join(normalized)
    if len(raw) <= max_length:
        return raw

    digest = sha256(raw.encode("utf-8")).hexdigest()[:_HASH_HEX_LENGTH]
    suffix = f":h:{digest}"
    prefix_budget = max(1, max_length - len(suffix))
    prefix = raw[:prefix_budget].rstrip(":")
    if not prefix:
        prefix = raw[:prefix_budget]
    return f"{prefix}{suffix}"
