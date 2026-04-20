"""Integration tests for scoped memory tool plugins."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.channel_routing.contracts import ChannelBindingRule
from afkbot.services.channel_routing.service import get_channel_binding_service
from afkbot.services.memory import reset_memory_services
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.plugins.memory_search import plugin as memory_search_plugin
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings, get_settings


def _user_facing_ctx(*, peer_id: str = "100") -> ToolContext:
    return ToolContext(
        profile_id="default",
        session_id=f"chat:{peer_id}",
        run_id=1,
        runtime_metadata={
            "transport": "telegram_user",
            "account_id": "personal-user",
            "peer_id": peer_id,
            "channel_binding": {"binding_id": "personal-user", "session_policy": "per-chat"},
        },
    )


def _trusted_ctx() -> ToolContext:
    return ToolContext(
        profile_id="default",
        session_id="cli:memory",
        run_id=2,
        runtime_metadata={"transport": "cli"},
    )


def _user_facing_thread_ctx(
    *, peer_id: str = "100", thread_id: str = "77", user_id: str = "500"
) -> ToolContext:
    return ToolContext(
        profile_id="default",
        session_id=f"thread:{peer_id}:{thread_id}:{user_id}",
        run_id=3,
        runtime_metadata={
            "transport": "telegram",
            "account_id": "support-bot",
            "peer_id": peer_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "channel_binding": {
                "binding_id": "support-topic",
                "session_policy": "per-user-in-group",
            },
        },
    )


def _metadata_missing_transport_ctx(*, peer_id: str = "100") -> ToolContext:
    return ToolContext(
        profile_id="default",
        session_id=f"chat:{peer_id}",
        run_id=4,
        runtime_metadata={
            "account_id": "personal-user",
            "peer_id": peer_id,
            "channel_binding": {"binding_id": "personal-user", "session_policy": "per-chat"},
        },
    )


async def _prepare(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> tuple[Settings, AsyncEngine, ToolRegistry]:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'tools_memory.db'}"
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", db_url)
    get_settings.cache_clear()
    reset_memory_services()

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        profiles = ProfileRepository(session)
        await profiles.get_or_create_default("default")
        await profiles.get_or_create_default("other")

    return settings, engine, ToolRegistry.from_settings(settings)


async def test_memory_plugins_crud_flow(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Scoped memory plugins should support upsert/search/list/delete lifecycle."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = _user_facing_ctx(peer_id="100")

        upsert_tool = registry.get("memory.upsert")
        assert upsert_tool is not None
        upsert_params = upsert_tool.parse_params(
            {
                "profile_key": "default",
                "memory_key": "favorite_book",
                "summary": "User likes Dune in this chat",
                "memory_kind": "preference",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        upsert_result = await upsert_tool.execute(ctx, upsert_params)
        assert upsert_result.ok is True
        assert upsert_result.payload["item"]["scope_kind"] == "chat"

        search_tool = registry.get("memory.search")
        assert search_tool is not None
        search_params = search_tool.parse_params(
            {"profile_key": "default", "query": "what book does user like", "limit": 5},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        search_result = await search_tool.execute(ctx, search_params)
        assert search_result.ok is True
        items = search_result.payload["items"]
        assert isinstance(items, list)
        assert len(items) == 1
        assert items[0]["memory_key"] == "favorite_book"
        assert items[0]["scope_kind"] == "chat"

        list_tool = registry.get("memory.list")
        assert list_tool is not None
        list_params = list_tool.parse_params(
            {"profile_key": "default", "limit": 10},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        list_result = await list_tool.execute(ctx, list_params)
        assert list_result.ok is True
        assert len(list_result.payload["items"]) == 1

        digest_tool = registry.get("memory.digest")
        assert digest_tool is not None
        digest_params = digest_tool.parse_params(
            {"profile_key": "default", "limit": 10},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        digest_result = await digest_tool.execute(ctx, digest_params)
        assert digest_result.ok is True
        assert digest_result.payload["item_count"] == 1
        assert "favorite_book" in digest_result.payload["digest_md"]

        promote_tool = registry.get("memory.promote")
        assert promote_tool is not None
        promote_params = promote_tool.parse_params(
            {"profile_key": "default", "memory_key": "favorite_book"},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        promote_result = await promote_tool.execute(ctx, promote_params)
        assert promote_result.ok is False
        assert promote_result.error_code == "memory_cross_scope_forbidden"

        delete_tool = registry.get("memory.delete")
        assert delete_tool is not None
        delete_params = delete_tool.parse_params(
            {"profile_key": "default", "memory_key": "favorite_book"},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        delete_result = await delete_tool.execute(ctx, delete_params)
        assert delete_result.ok is True

        delete_again_result = await delete_tool.execute(ctx, delete_params)
        assert delete_again_result.ok is False
        assert delete_again_result.error_code == "memory_not_found"
    finally:
        await engine.dispose()


async def test_memory_plugins_profile_key_mismatch(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Mismatched profile_key should return strict profile_not_found."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = _user_facing_ctx(peer_id="100")
        search_tool = registry.get("memory.search")
        assert search_tool is not None
        params = search_tool.parse_params(
            {"profile_key": "other", "query": "hello"},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await search_tool.execute(ctx, params)
        assert result.ok is False
        assert result.error_code == "profile_not_found"
    finally:
        await engine.dispose()


async def test_user_facing_memory_search_cannot_jump_to_other_chat(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """User-facing channels should not be able to query another chat scope explicitly."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        upsert_tool = registry.get("memory.upsert")
        assert upsert_tool is not None
        seed_ctx = _user_facing_ctx(peer_id="200")
        seed_params = upsert_tool.parse_params(
            {
                "profile_key": "default",
                "memory_key": "private_note",
                "summary": "Other chat private fact",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        seed_result = await upsert_tool.execute(seed_ctx, seed_params)
        assert seed_result.ok is True

        search_tool = registry.get("memory.search")
        assert search_tool is not None
        ctx = _user_facing_ctx(peer_id="100")
        params = search_tool.parse_params(
            {
                "profile_key": "default",
                "scope": "chat",
                "peer_id": "200",
                "query": "private fact",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await search_tool.execute(ctx, params)
        assert result.ok is False
        assert result.error_code == "memory_cross_scope_forbidden"
    finally:
        await engine.dispose()


async def test_memory_search_enforces_explicit_scope_guard(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, registry = await _prepare(tmp_path, monkeypatch)

    def _forbidden_guard(*, ctx, requested_scope, operation):
        return ("memory_cross_scope_forbidden", "forced guard denial")

    monkeypatch.setattr(
        memory_search_plugin,
        "ensure_memory_scope_allowed",
        _forbidden_guard,
    )

    try:
        search_tool = registry.get("memory.search")
        assert search_tool is not None
        params = search_tool.parse_params(
            {"profile_key": "default", "scope": "chat", "query": "private fact"},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        result = await search_tool.execute(_user_facing_ctx(peer_id="100"), params)
        assert result.ok is False
        assert result.error_code == "memory_cross_scope_forbidden"
    finally:
        await engine.dispose()


async def test_user_facing_memory_search_allows_current_thread_scope_with_user_id(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """User-facing turns may explicitly target their current thread scope even when user_id is present."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        upsert_tool = registry.get("memory.upsert")
        search_tool = registry.get("memory.search")
        assert upsert_tool is not None
        assert search_tool is not None

        ctx = _user_facing_thread_ctx()
        upsert_result = await upsert_tool.execute(
            ctx,
            upsert_tool.parse_params(
                {
                    "profile_key": "default",
                    "scope": "thread",
                    "memory_key": "topic-deadline",
                    "summary": "Current thread deadline is March 15",
                    "memory_kind": "decision",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert upsert_result.ok is True
        assert upsert_result.payload["item"]["scope_kind"] == "thread"

        search_result = await search_tool.execute(
            ctx,
            search_tool.parse_params(
                {
                    "profile_key": "default",
                    "scope": "thread",
                    "query": "March 15 deadline",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert search_result.ok is True
        assert search_result.payload["items"][0]["memory_key"] == "topic-deadline"
        assert search_result.payload["items"][0]["scope_kind"] == "thread"
    finally:
        await engine.dispose()


async def test_user_facing_memory_search_fails_closed_when_transport_metadata_missing(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """User-facing turns with missing transport metadata should not bypass cross-scope guards."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        search_tool = registry.get("memory.search")
        assert search_tool is not None
        result = await search_tool.execute(
            _metadata_missing_transport_ctx(peer_id="100"),
            search_tool.parse_params(
                {
                    "profile_key": "default",
                    "scope": "chat",
                    "transport": "telegram_user",
                    "account_id": "personal-user",
                    "peer_id": "200",
                    "query": "private fact",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert result.ok is False
        assert result.error_code == "memory_cross_scope_forbidden"
    finally:
        await engine.dispose()


async def test_trusted_memory_search_can_target_binding_scope(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Trusted surfaces should be able to resolve a concrete foreign chat scope via binding id."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        await get_channel_binding_service(settings).put(
            ChannelBindingRule(
                binding_id="telegram-sales",
                transport="telegram_user",
                profile_id="default",
                session_policy="per-chat",
                account_id="personal-user",
                peer_id="200",
            )
        )

        upsert_tool = registry.get("memory.upsert")
        assert upsert_tool is not None
        seed_result = await upsert_tool.execute(
            _user_facing_ctx(peer_id="200"),
            upsert_tool.parse_params(
                {
                    "profile_key": "default",
                    "memory_key": "sales-note",
                    "summary": "Client in sales chat prefers Telegram-first workflow",
                    "memory_kind": "preference",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert seed_result.ok is True

        search_tool = registry.get("memory.search")
        assert search_tool is not None
        result = await search_tool.execute(
            _trusted_ctx(),
            search_tool.parse_params(
                {
                    "profile_key": "default",
                    "binding_id": "telegram-sales",
                    "query": "Telegram-first workflow",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert result.ok is True
        assert result.payload["items"][0]["memory_key"] == "sales-note"
        assert result.payload["items"][0]["scope_kind"] == "chat"
    finally:
        await engine.dispose()


async def test_trusted_memory_search_rejects_broad_binding_scope(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Binding-based trusted lookup should fail when the binding is too broad to identify one chat."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        await get_channel_binding_service(settings).put(
            ChannelBindingRule(
                binding_id="telegram-broad",
                transport="telegram_user",
                profile_id="default",
                session_policy="per-chat",
                account_id="personal-user",
            )
        )
        search_tool = registry.get("memory.search")
        assert search_tool is not None
        result = await search_tool.execute(
            _trusted_ctx(),
            search_tool.parse_params(
                {
                    "profile_key": "default",
                    "binding_id": "telegram-broad",
                    "query": "anything",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert result.ok is False
        assert result.error_code == "memory_scope_binding_too_broad"
    finally:
        await engine.dispose()


async def test_trusted_memory_digest_can_include_promoted_global(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Trusted digest should combine local scope and promoted-global memory deterministically."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        upsert_tool = registry.get("memory.upsert")
        digest_tool = registry.get("memory.digest")
        promote_tool = registry.get("memory.promote")
        assert upsert_tool is not None
        assert digest_tool is not None
        assert promote_tool is not None

        local_ctx = _user_facing_ctx(peer_id="200")
        local_seed = await upsert_tool.execute(
            local_ctx,
            upsert_tool.parse_params(
                {
                    "profile_key": "default",
                    "memory_key": "chat-style",
                    "summary": "This chat prefers concise Russian replies.",
                    "memory_kind": "preference",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert local_seed.ok is True
        promoted = await promote_tool.execute(
            _trusted_ctx(),
            promote_tool.parse_params(
                {
                    "profile_key": "default",
                    "scope": "chat",
                    "transport": "telegram_user",
                    "account_id": "personal-user",
                    "peer_id": "200",
                    "memory_key": "chat-style",
                    "target_memory_key": "global-style",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert promoted.ok is True

        digest_result = await digest_tool.execute(
            _trusted_ctx(),
            digest_tool.parse_params(
                {
                    "profile_key": "default",
                    "scope": "chat",
                    "transport": "telegram_user",
                    "account_id": "personal-user",
                    "peer_id": "200",
                    "include_global": True,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert digest_result.ok is True
        assert digest_result.payload["local_count"] == 1
        assert digest_result.payload["global_count"] == 1
        assert "## Current Scope" in digest_result.payload["digest_md"]
        assert "## Promoted Global" in digest_result.payload["digest_md"]
    finally:
        await engine.dispose()


async def test_memory_search_keeps_local_scope_ahead_of_global_fallback_and_respects_limit(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Local scope hits should remain first, and merged global fallback must still honor limit."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        upsert_tool = registry.get("memory.upsert")
        search_tool = registry.get("memory.search")
        assert upsert_tool is not None
        assert search_tool is not None

        local_result = await upsert_tool.execute(
            _user_facing_ctx(peer_id="100"),
            upsert_tool.parse_params(
                {
                    "profile_key": "default",
                    "memory_key": "deploy_policy",
                    "summary": "For this chat, deploy to staging first.",
                    "memory_kind": "decision",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert local_result.ok is True

        global_result = await upsert_tool.execute(
            _trusted_ctx(),
            upsert_tool.parse_params(
                {
                    "profile_key": "default",
                    "scope": "profile",
                    "memory_key": "deploy_policy",
                    "summary": "Globally, deploy directly to production.",
                    "memory_kind": "decision",
                    "visibility": "promoted_global",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert global_result.ok is True

        result = await search_tool.execute(
            _user_facing_ctx(peer_id="100"),
            search_tool.parse_params(
                {
                    "profile_key": "default",
                    "query": "deploy_policy",
                    "include_global": True,
                    "limit": 1,
                    "global_limit": 1,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert result.ok is True
        items = result.payload["items"]
        assert isinstance(items, list)
        assert len(items) == 1
        assert items[0]["memory_key"] == "deploy_policy"
        assert items[0]["scope_kind"] == "chat"
    finally:
        await engine.dispose()


async def test_memory_search_preserves_local_first_even_when_global_key_matches_exactly(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Exact-key promoted globals should not evict semantically relevant local hits."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        upsert_tool = registry.get("memory.upsert")
        search_tool = registry.get("memory.search")
        assert upsert_tool is not None
        assert search_tool is not None

        local_result = await upsert_tool.execute(
            _user_facing_ctx(peer_id="100"),
            upsert_tool.parse_params(
                {
                    "profile_key": "default",
                    "memory_key": "staging_flow",
                    "summary": "Deployment flow for this chat uses staging before production.",
                    "memory_kind": "decision",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert local_result.ok is True

        global_result = await upsert_tool.execute(
            _trusted_ctx(),
            upsert_tool.parse_params(
                {
                    "profile_key": "default",
                    "scope": "profile",
                    "memory_key": "deploy_policy",
                    "summary": "Globally, deploy directly to production.",
                    "memory_kind": "decision",
                    "visibility": "promoted_global",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert global_result.ok is True

        result = await search_tool.execute(
            _user_facing_ctx(peer_id="100"),
            search_tool.parse_params(
                {
                    "profile_key": "default",
                    "query": "deployment flow",
                    "include_global": True,
                    "limit": 1,
                    "global_limit": 1,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert result.ok is True
        items = result.payload["items"]
        assert isinstance(items, list)
        assert len(items) == 1
        assert items[0]["memory_key"] == "staging_flow"
        assert items[0]["scope_kind"] == "chat"
    finally:
        await engine.dispose()
