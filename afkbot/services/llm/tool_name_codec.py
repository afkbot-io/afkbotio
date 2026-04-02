"""Helpers for encoding provider-safe tool names and decoding them back."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import re

_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def build_tool_name_codec(
    *,
    visible_tool_names: Iterable[str],
    historical_tool_names: Iterable[str] = (),
) -> tuple[Callable[[str], str], Callable[[str], str]]:
    """Build provider-safe encode/decode helpers for the visible tool surface."""

    encode_map: dict[str, str] = {}
    decode_map: dict[str, str] = {}
    used_names: set[str] = set()

    normalized_visible = _normalize_tool_names(visible_tool_names)
    for original in normalized_visible:
        encoded = reserve_tool_name(original, used_names)
        encode_map[original] = encoded
        decode_map[encoded] = original
    normalized_historical = _normalize_tool_names(historical_tool_names)
    for original in normalized_historical:
        if original in encode_map:
            continue
        encode_map[original] = reserve_tool_name(original, used_names)

    def encode(name: str) -> str:
        normalized = name.strip()
        if not normalized:
            return normalized
        encoded = encode_map.get(normalized)
        if encoded is None:
            raise ValueError(f"Unknown tool name for provider payload: {normalized}")
        return encoded

    def decode(name: str) -> str:
        normalized = name.strip()
        if not normalized:
            return normalized
        decoded = decode_map.get(normalized)
        if decoded is None:
            raise ValueError(f"Unknown provider tool name: {normalized}")
        return decoded

    return encode, decode


def reserve_tool_name(original: str, used_names: set[str]) -> str:
    """Reserve one unique provider-safe tool name for a visible tool."""

    if _TOOL_NAME_PATTERN.fullmatch(original) and original not in used_names:
        used_names.add(original)
        return original

    base = sanitize_tool_name(original)
    candidate = base
    suffix = 2
    while candidate in used_names:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used_names.add(candidate)
    return candidate


def sanitize_tool_name(value: str) -> str:
    """Convert one tool name to the provider-safe character set."""

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return normalized or "tool"


def _normalize_tool_names(values: Iterable[str]) -> tuple[str, ...]:
    """Return unique trimmed tool names preserving first-seen order."""

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = str(value).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return tuple(normalized)
