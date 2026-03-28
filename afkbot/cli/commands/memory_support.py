"""Shared support helpers for memory CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import TypeAlias, cast, get_args

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.runtime_assets_common import resolve_inline_or_file_text
from afkbot.services.memory import (
    MemoryKind,
    MemoryItemMetadata,
    MemoryScopeDescriptor,
    MemoryScopeMode,
    MemorySourceKind,
)
from afkbot.services.memory.runtime_scope import resolve_requested_scope
from afkbot.settings import get_settings

SerializedMemoryItem: TypeAlias = dict[str, object]


async def resolve_cli_scope(
    *,
    profile_id: str,
    scope: MemoryScopeMode,
    binding_id: str | None,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
    session_id: str | None,
) -> MemoryScopeDescriptor:
    """Resolve one CLI-selected scope using the same rules as runtime tools."""

    return await resolve_requested_scope(
        settings=get_settings(),
        profile_id=profile_id,
        session_id=session_id or "cli:memory",
        runtime_metadata=None,
        scope_mode=scope,
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
        requested_session_id=session_id,
        binding_id=binding_id,
    )


def resolve_optional_text_or_file(*, text: str | None, from_file: Path | None) -> str | None:
    """Resolve optional text/file input while still forbidding ambiguous double inputs."""

    if text is None and from_file is None:
        return None
    return resolve_inline_or_file_text(text=text, from_file=from_file)


def serialize_memory_items(items: list[MemoryItemMetadata]) -> list[SerializedMemoryItem]:
    """Convert metadata models into JSON-safe payload rows for CLI output."""

    return [cast(SerializedMemoryItem, item.model_dump(mode="json")) for item in items]


def filter_items(
    *,
    items: list[SerializedMemoryItem],
    memory_kinds: tuple[MemoryKind, ...],
    source_kinds: tuple[MemorySourceKind, ...],
) -> list[SerializedMemoryItem]:
    """Apply optional semantic kind/source kind filters to serialized CLI payload rows."""

    payload_items = list(items)
    if memory_kinds:
        allowed_memory_kinds = set(memory_kinds)
        payload_items = [
            item for item in payload_items if str(item.get("memory_kind") or "") in allowed_memory_kinds
        ]
    if source_kinds:
        allowed_source_kinds = set(source_kinds)
        payload_items = [
            item for item in payload_items if str(item.get("source_kind") or "") in allowed_source_kinds
        ]
    return payload_items


def filter_memory_metadata(
    *,
    items: list[MemoryItemMetadata],
    memory_kinds: tuple[MemoryKind, ...],
    source_kinds: tuple[MemorySourceKind, ...],
) -> list[MemoryItemMetadata]:
    """Apply optional semantic kind/source kind filters to metadata rows before rendering."""

    filtered = list(items)
    if memory_kinds:
        allowed_memory_kinds = set(memory_kinds)
        filtered = [item for item in filtered if getattr(item, "memory_kind", None) in allowed_memory_kinds]
    if source_kinds:
        allowed_source_kinds = set(source_kinds)
        filtered = [item for item in filtered if getattr(item, "source_kind", None) in allowed_source_kinds]
    return filtered


def normalize_memory_kinds(values: list[str]) -> tuple[MemoryKind, ...]:
    """Validate and normalize CLI repeatable memory kinds."""

    return cast(tuple[MemoryKind, ...], normalize_choice(values=values, allowed=get_args(MemoryKind)))


def normalize_source_kinds(values: list[str]) -> tuple[MemorySourceKind, ...]:
    """Validate and normalize CLI repeatable source kinds."""

    return cast(
        tuple[MemorySourceKind, ...],
        normalize_choice(values=values, allowed=get_args(MemorySourceKind)),
    )


def normalize_choice(*, values: list[str], allowed: tuple[object, ...]) -> tuple[str, ...]:
    """Normalize repeatable string choices against one allowed value set."""

    allowed_values = {str(item) for item in allowed}
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = raw.strip().lower()
        if not value or value in seen:
            continue
        if value not in allowed_values:
            raise_usage_error(
                f"Unsupported value `{raw}`. Allowed: {', '.join(sorted(allowed_values))}."
            )
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def merge_search_hits(
    local_items: list[SerializedMemoryItem],
    global_items: list[SerializedMemoryItem],
) -> list[SerializedMemoryItem]:
    """Append global fallback hits after local hits without duplicating exact scope/key pairs."""

    seen = {
        (str(item.get("scope_key") or ""), str(item.get("memory_key") or ""))
        for item in local_items
    }
    merged = list(local_items)
    for item in global_items:
        dedupe_key = (str(item.get("scope_key") or ""), str(item.get("memory_key") or ""))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        merged.append(item)
    return merged


__all__ = [
    "SerializedMemoryItem",
    "filter_items",
    "filter_memory_metadata",
    "merge_search_hits",
    "normalize_choice",
    "normalize_memory_kinds",
    "normalize_source_kinds",
    "resolve_cli_scope",
    "resolve_optional_text_or_file",
    "serialize_memory_items",
]
