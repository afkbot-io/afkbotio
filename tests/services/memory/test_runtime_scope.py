"""Tests for scoped memory runtime selector resolution."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.channel_routing.contracts import ChannelBindingRule
from afkbot.services.channel_routing.service import (
    get_channel_binding_service,
    reset_channel_binding_services_async,
)
from afkbot.services.memory import reset_memory_services_async
from afkbot.services.memory.runtime_scope import (
    MemoryScopeResolutionError,
    resolve_requested_scope,
    resolve_runtime_scope,
)
from afkbot.settings import Settings, get_settings


async def _prepare(tmp_path: Path, monkeypatch: MonkeyPatch) -> tuple[Settings, AsyncEngine]:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'runtime_scope.db'}"
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", db_url)
    get_settings.cache_clear()
    await reset_memory_services_async()
    await reset_channel_binding_services_async()

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        profiles = ProfileRepository(session)
        await profiles.get_or_create_default("default")
        await profiles.get_or_create_default("other")
    return settings, engine


async def test_resolve_runtime_scope_auto_prefers_user_in_chat_with_thread() -> None:
    """Auto scope should prefer user-in-chat over plain thread/chat when user_id is present."""

    scope = resolve_runtime_scope(
        session_id="chat:42",
        runtime_metadata={
            "transport": "telegram",
            "account_id": "support-bot",
            "peer_id": "-100123",
            "thread_id": "77",
            "user_id": "500",
            "channel_binding": {"binding_id": "tg-main", "session_policy": "per-user-in-group"},
        },
        scope_mode="auto",
    )

    assert scope.scope_kind == "user_in_chat"
    assert scope.thread_id == "77"
    assert scope.user_id == "500"
    assert scope.binding_id == "tg-main"


async def test_resolve_runtime_scope_auto_uses_synthetic_chat_scope_for_watcher_turns() -> None:
    """Watcher digest turns should keep a local synthetic chat scope instead of falling back to profile-global."""

    scope = resolve_runtime_scope(
        session_id="telegram_user_watch:telethon-main",
        runtime_metadata={
            "transport": "telegram_user",
            "account_id": "personal-user",
            "peer_id": "__watcher__:telethon-main",
            "telethon_watcher": {
                "endpoint_id": "telethon-main",
                "event_count": 3,
            },
        },
        scope_mode="auto",
    )

    assert scope.scope_kind == "chat"
    assert scope.transport == "telegram_user"
    assert scope.account_id == "personal-user"
    assert scope.peer_id == "__watcher__:telethon-main"


async def test_resolve_requested_scope_can_expand_binding_id_for_trusted_access(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Trusted explicit lookup should resolve a concrete chat scope from one binding id."""

    settings, engine = await _prepare(tmp_path, monkeypatch)
    try:
        binding_service = get_channel_binding_service(settings)
        await binding_service.put(
            ChannelBindingRule(
                binding_id="telegram-sales",
                transport="telegram",
                profile_id="default",
                session_policy="per-thread",
                account_id="support-bot",
                peer_id="-100123",
                thread_id="77",
            )
        )

        scope = await resolve_requested_scope(
            settings=settings,
            profile_id="default",
            session_id="cli:memory",
            runtime_metadata=None,
            scope_mode="auto",
            transport=None,
            account_id=None,
            peer_id=None,
            thread_id=None,
            user_id=None,
            requested_session_id=None,
            binding_id="telegram-sales",
        )

        assert scope.scope_kind == "thread"
        assert scope.transport == "telegram"
        assert scope.account_id == "support-bot"
        assert scope.peer_id == "-100123"
        assert scope.thread_id == "77"
        assert scope.binding_id == "telegram-sales"
    finally:
        await engine.dispose()


async def test_resolve_requested_scope_rejects_broad_binding_id(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Binding-based scope lookup should fail closed when the binding is too broad."""

    settings, engine = await _prepare(tmp_path, monkeypatch)
    try:
        binding_service = get_channel_binding_service(settings)
        await binding_service.put(
            ChannelBindingRule(
                binding_id="telegram-broad",
                transport="telegram_user",
                profile_id="default",
                session_policy="per-chat",
                account_id="personal-user",
            )
        )

        try:
            await resolve_requested_scope(
                settings=settings,
                profile_id="default",
                session_id="cli:memory",
                runtime_metadata=None,
                scope_mode="auto",
                transport=None,
                account_id=None,
                peer_id=None,
                thread_id=None,
                user_id=None,
                requested_session_id=None,
                binding_id="telegram-broad",
            )
        except MemoryScopeResolutionError as exc:
            assert exc.error_code == "memory_scope_binding_too_broad"
        else:
            raise AssertionError("expected MemoryScopeResolutionError")
    finally:
        await engine.dispose()


async def test_resolve_requested_scope_rejects_conflicting_binding_and_selectors(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Binding-based lookup should fail when explicit selectors disagree with the binding."""

    settings, engine = await _prepare(tmp_path, monkeypatch)
    try:
        binding_service = get_channel_binding_service(settings)
        await binding_service.put(
            ChannelBindingRule(
                binding_id="telegram-sales",
                transport="telegram",
                profile_id="default",
                session_policy="per-chat",
                account_id="support-bot",
                peer_id="-100123",
            )
        )

        try:
            await resolve_requested_scope(
                settings=settings,
                profile_id="default",
                session_id="cli:memory",
                runtime_metadata=None,
                scope_mode="auto",
                transport=None,
                account_id=None,
                peer_id="-100999",
                thread_id=None,
                user_id=None,
                requested_session_id=None,
                binding_id="telegram-sales",
            )
        except MemoryScopeResolutionError as exc:
            assert exc.error_code == "memory_scope_binding_conflict"
        else:
            raise AssertionError("expected MemoryScopeResolutionError")
    finally:
        await engine.dispose()
