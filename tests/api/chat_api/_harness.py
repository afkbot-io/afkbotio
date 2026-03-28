"""Shared helpers for chat API route tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pytest import MonkeyPatch

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.connect import ConnectAccessTokenContext
from afkbot.settings import Settings


def auth_headers(token: str = "acc-1", proof: str = "proof-1") -> dict[str, str]:
    """Build standard chat API auth headers used by route tests."""

    return {
        "Authorization": f"Bearer {token}",
        "X-AFK-Session-Proof": proof,
    }


def patch_valid_chat_access_token(
    monkeypatch: MonkeyPatch,
    *,
    profile_id: str = "default",
    session_id: str = "api-s",
    token: str = "acc-1",
) -> None:
    """Patch connect token validation with a deterministic successful context."""

    async def _fake_validate(
        *,
        access_token: str,
        session_proof: str | None = None,
        session_factory: object | None = None,
    ) -> ConnectAccessTokenContext:
        assert access_token == token
        assert session_proof == "proof-1"
        _ = session_factory
        return ConnectAccessTokenContext(
            profile_id=profile_id,
            session_id=session_id,
            base_url="http://127.0.0.1:8081",
            expires_at=datetime.now(tz=UTC),
        )

    monkeypatch.setattr("afkbot.api.chat_auth.validate_connect_access_token", _fake_validate)


def patch_api_settings(monkeypatch: MonkeyPatch, tmp_path: Path) -> Settings:
    """Patch shared API settings getters to one isolated sqlite database."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'chat_api.db'}",
    )
    monkeypatch.setattr("afkbot.api.app.get_settings", lambda: settings)
    monkeypatch.setattr("afkbot.api.chat_routes.http.get_settings", lambda: settings)
    monkeypatch.setattr("afkbot.api.chat_routes.scope.get_settings", lambda: settings)
    monkeypatch.setattr("afkbot.api.chat_routes.websocket.get_settings", lambda: settings)
    monkeypatch.setattr("afkbot.services.agent_loop.api_runtime.get_settings", lambda: settings)
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    return settings


async def seed_profile(settings: Settings, *, profile_id: str = "default") -> None:
    """Create one profile row for real connect-token route tests."""

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default(profile_id)
    finally:
        await engine.dispose()


def seed_profile_sync(settings: Settings, *, profile_id: str = "default") -> None:
    """Sync wrapper around `seed_profile` for regular pytest tests."""

    asyncio.run(seed_profile(settings, profile_id=profile_id))
