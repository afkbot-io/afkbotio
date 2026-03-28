"""Tests for deterministic scoped memory digest rendering."""

from __future__ import annotations

from datetime import UTC, datetime

from afkbot.services.memory import MemoryScopeDescriptor
from afkbot.services.memory.contracts import MemoryItemMetadata
from afkbot.services.memory.digest import render_memory_digest


def _item(
    *,
    item_id: int,
    memory_key: str,
    scope_kind: str,
    scope_key: str,
    summary: str,
    memory_kind: str = "note",
    visibility: str = "local",
) -> MemoryItemMetadata:
    now = datetime.now(UTC)
    return MemoryItemMetadata(
        id=item_id,
        profile_id="default",
        memory_key=memory_key,
        scope_kind=scope_kind,
        scope_key=scope_key,
        transport="telegram_user" if scope_kind != "profile" else None,
        account_id="personal-user" if scope_kind != "profile" else None,
        peer_id="100" if scope_kind != "profile" else None,
        source="test",
        source_kind="manual",
        memory_kind=memory_kind,
        visibility=visibility,
        summary=summary,
        details_md=None,
        content=summary,
        created_at=now,
        updated_at=now,
    )


def test_render_memory_digest_sections_local_and_global() -> None:
    """Digest should separate local and promoted-global memory items."""

    digest = render_memory_digest(
        scope=MemoryScopeDescriptor(
            scope_kind="chat",
            transport="telegram_user",
            account_id="personal-user",
            peer_id="100",
        ),
        local_items=[
            _item(
                item_id=1,
                memory_key="reply-style",
                scope_kind="chat",
                scope_key="scope=chat|transport=telegram_user|account_id=personal-user|peer_id=100",
                summary="This chat prefers short replies.",
                memory_kind="preference",
            )
        ],
        global_items=[
            _item(
                item_id=2,
                memory_key="product-fact",
                scope_kind="profile",
                scope_key="profile",
                summary="AFKBOT stores semantic memory in SQLite-backed storage.",
                memory_kind="fact",
                visibility="promoted_global",
            )
        ],
    )

    assert digest.item_count == 2
    assert digest.local_count == 1
    assert digest.global_count == 1
    assert digest.kind_counts == {"fact": 1, "preference": 1}
    assert "## Current Scope" in digest.digest_md
    assert "## Promoted Global" in digest.digest_md
    assert "**reply-style**" in digest.digest_md
    assert "**product-fact**" in digest.digest_md
