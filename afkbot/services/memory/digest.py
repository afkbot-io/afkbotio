"""Deterministic rendering helpers for scoped semantic memory digests."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, ConfigDict

from afkbot.services.memory.contracts import MemoryItemMetadata, MemoryScopeDescriptor

_KIND_ORDER: tuple[str, ...] = ("fact", "preference", "decision", "task", "risk", "note")


class MemoryDigestResult(BaseModel):
    """Rendered summary for one scoped memory digest request."""

    model_config = ConfigDict(extra="forbid")

    item_count: int
    local_count: int
    global_count: int
    kind_counts: dict[str, int]
    digest_md: str


def render_memory_digest(
    *,
    scope: MemoryScopeDescriptor,
    local_items: list[MemoryItemMetadata],
    global_items: list[MemoryItemMetadata] | None = None,
) -> MemoryDigestResult:
    """Render one operator-friendly digest from local and optional global memory hits."""

    merged_global = _dedupe_global_items(local_items=local_items, global_items=global_items or [])
    combined = [*local_items, *merged_global]
    kind_counts = Counter(str(item.memory_kind) for item in combined)
    lines: list[str] = [
        "# Memory Digest",
        "",
        f"- scope: {scope.scope_kind}",
        f"- local_items: {len(local_items)}",
        f"- global_items: {len(merged_global)}",
        f"- total_items: {len(combined)}",
    ]
    if local_items:
        lines.extend(["", "## Current Scope", ""])
        lines.extend(_render_sectioned_items(local_items))
    if merged_global:
        lines.extend(["", "## Promoted Global", ""])
        lines.extend(_render_sectioned_items(merged_global))
    if not combined:
        lines.extend(["", "No memory items matched this scope."])
    return MemoryDigestResult(
        item_count=len(combined),
        local_count=len(local_items),
        global_count=len(merged_global),
        kind_counts={kind: kind_counts[kind] for kind in _KIND_ORDER if kind_counts[kind] > 0},
        digest_md="\n".join(lines).strip(),
    )


def _render_sectioned_items(items: list[MemoryItemMetadata]) -> list[str]:
    lines: list[str] = []
    for kind in _KIND_ORDER:
        kind_items = [item for item in items if str(item.memory_kind) == kind]
        if not kind_items:
            continue
        lines.append(f"### {kind.replace('_', ' ').title()}")
        lines.append("")
        for item in kind_items:
            lines.append(_render_item_line(item))
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _render_item_line(item: MemoryItemMetadata) -> str:
    summary = _compact_text(item.summary or item.content or item.details_md or item.memory_key)
    visibility_suffix = " [global]" if item.visibility == "promoted_global" else ""
    return f"- **{item.memory_key}**: {summary}{visibility_suffix}"


def _compact_text(value: str, *, max_chars: int = 180) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _dedupe_global_items(
    *,
    local_items: list[MemoryItemMetadata],
    global_items: list[MemoryItemMetadata],
) -> list[MemoryItemMetadata]:
    seen = {(item.scope_key, item.memory_key) for item in local_items}
    merged: list[MemoryItemMetadata] = []
    for item in global_items:
        dedupe_key = (item.scope_key, item.memory_key)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        merged.append(item)
    return merged
