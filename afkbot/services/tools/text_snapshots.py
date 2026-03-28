"""Shared bounded text snapshot helpers for file-oriented tools."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.tools.workspace import truncate_utf8


def read_prefix_bytes(*, path: Path, max_bytes: int) -> bytes:
    """Read at most `max_bytes + 1` bytes from the start of one file."""

    read_limit = max(1, max_bytes) + 1
    data = bytearray()
    with path.open("rb") as handle:
        while len(data) < read_limit:
            chunk = handle.read(min(65536, read_limit - len(data)))
            if not chunk:
                break
            data.extend(chunk)
    return bytes(data)


def snapshot_path_text(*, path: Path, max_bytes: int) -> tuple[str, bool, int]:
    """Return bounded decoded text, truncation flag, and original byte size for one file."""

    raw_prefix = read_prefix_bytes(path=path, max_bytes=max_bytes)
    content, truncated = truncate_utf8(raw=raw_prefix, max_bytes=max_bytes)
    return content, truncated, path.stat().st_size


def snapshot_inline_text(*, text: str, max_bytes: int) -> tuple[str, bool, int]:
    """Return bounded decoded text, truncation flag, and UTF-8 byte size for inline text."""

    read_limit = max(1, max_bytes) + 1
    prefix = bytearray()
    size_bytes = 0
    for character in text:
        encoded = character.encode("utf-8")
        size_bytes += len(encoded)
        remaining = read_limit - len(prefix)
        if remaining > 0:
            prefix.extend(encoded[:remaining])
    content, truncated = truncate_utf8(raw=bytes(prefix), max_bytes=max_bytes)
    return content, truncated or size_bytes > max_bytes, size_bytes
