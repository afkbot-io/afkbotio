"""Tests for connect service lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import update

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.connect_claim_token import ConnectClaimToken
from afkbot.models.connect_session_token import ConnectSessionToken
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.connect import (
    ConnectServiceError,
    ConnectClientMetadata,
    claim_connect_token,
    issue_connect_url,
    refresh_connect_access_token,
    revoke_connect_session,
    validate_connect_access_token,
)
from afkbot.settings import Settings


async def _seed_profile(settings: Settings, *, profile_id: str = "default") -> None:
    """Create one runtime profile row for connect lifecycle tests."""

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        async with session_scope(factory) as db:
            await ProfileRepository(db).get_or_create_default(profile_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_connect_issue_claim_refresh_revoke_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Service should support end-to-end connect token lifecycle."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    await _seed_profile(settings)

    issued = await issue_connect_url(
        profile_id="default",
        session_id="desktop-session",
        base_url="http://127.0.0.1:8081",
        ttl_sec=120,
        context_overrides=TurnContextOverrides(
            runtime_metadata={
                "transport": "desktop",
                "peer_id": "workspace-7",
                "channel_binding": {
                    "binding_id": "desktop-sales",
                    "session_policy": "per-thread",
                },
            },
            prompt_overlay="Always respond as the desktop workspace assistant.",
        ),
    )
    parsed = urlparse(issued.connect_url)
    params = parse_qs(parsed.query)
    claim_token = params["claim_token"][0]

    claimed = await claim_connect_token(
        claim_token=claim_token,
        client=ConnectClientMetadata(platform="desktop", app_version="1.2.3"),
    )
    assert claimed.profile_id == "default"
    assert claimed.session_id == "desktop-session"
    assert claimed.base_url == "http://127.0.0.1:8081"
    assert claimed.expires_in_sec == 3600
    assert claimed.access_token
    assert claimed.refresh_token
    assert claimed.session_proof
    claimed_scope = await validate_connect_access_token(
        access_token=claimed.access_token,
        session_proof=claimed.session_proof,
    )
    assert claimed_scope.profile_id == "default"
    assert claimed_scope.session_id == "desktop-session"
    assert claimed_scope.runtime_metadata == {
        "transport": "desktop",
        "peer_id": "workspace-7",
        "channel_binding": {
            "binding_id": "desktop-sales",
            "session_policy": "per-thread",
        },
        "client": {
            "platform": "desktop",
            "app_version": "1.2.3",
        },
    }
    assert claimed_scope.prompt_overlay == "Always respond as the desktop workspace assistant."

    with pytest.raises(ConnectServiceError) as replay_exc:
        await claim_connect_token(claim_token=claim_token)
    assert replay_exc.value.error_code == "connect_token_used"

    refreshed = await refresh_connect_access_token(
        refresh_token=claimed.refresh_token,
        session_proof=claimed.session_proof,
    )
    assert refreshed.access_token
    assert refreshed.refresh_token
    assert refreshed.expires_in_sec == 3600
    assert refreshed.session_id == "desktop-session"
    refreshed_scope = await validate_connect_access_token(
        access_token=refreshed.access_token,
        session_proof=claimed.session_proof,
    )
    assert refreshed_scope.profile_id == "default"
    assert refreshed_scope.session_id == "desktop-session"
    assert refreshed_scope.runtime_metadata == claimed_scope.runtime_metadata
    assert refreshed_scope.prompt_overlay == claimed_scope.prompt_overlay

    with pytest.raises(ConnectServiceError) as old_refresh_exc:
        await refresh_connect_access_token(
            refresh_token=claimed.refresh_token,
            session_proof=claimed.session_proof,
        )
    assert old_refresh_exc.value.error_code == "connect_refresh_token_invalid"

    revoked = await revoke_connect_session(
        refresh_token=refreshed.refresh_token,
        session_proof=claimed.session_proof,
    )
    assert revoked is True

    with pytest.raises(ConnectServiceError) as revoked_exc:
        await refresh_connect_access_token(
            refresh_token=refreshed.refresh_token,
            session_proof=claimed.session_proof,
        )
    assert revoked_exc.value.error_code == "connect_refresh_token_revoked"

    with pytest.raises(ConnectServiceError) as access_revoked_exc:
        await validate_connect_access_token(
            access_token=claimed.access_token,
            session_proof=claimed.session_proof,
        )
    assert access_revoked_exc.value.error_code == "connect_access_token_revoked"


@pytest.mark.asyncio
async def test_connect_claim_rejects_expired_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Claim should fail when claim token expiry timestamp is in the past."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-expired.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    await _seed_profile(settings)

    issued = await issue_connect_url(
        profile_id="default",
        session_id="desktop-session",
        base_url="http://127.0.0.1:8081",
        ttl_sec=120,
    )
    claim_token = parse_qs(urlparse(issued.connect_url).query)["claim_token"][0]

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        async with session_scope(factory) as db:
            await db.execute(
                update(ConnectClaimToken).values(expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
            )
    finally:
        await engine.dispose()

    with pytest.raises(ConnectServiceError) as exc:
        await claim_connect_token(claim_token=claim_token)
    assert exc.value.error_code == "connect_token_expired"


@pytest.mark.asyncio
async def test_connect_issue_rejects_invalid_base_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Service should fail closed for unsupported base-url schemes."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-invalid-base.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    await _seed_profile(settings)

    with pytest.raises(ConnectServiceError) as exc:
        await issue_connect_url(
            profile_id="default",
            session_id="desktop-session",
            base_url="ftp://127.0.0.1:8081",
            ttl_sec=120,
        )
    assert exc.value.error_code == "connect_base_url_invalid"


@pytest.mark.asyncio
async def test_connect_issue_rejects_insecure_public_http_base_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Public connect URLs should require HTTPS instead of raw HTTP."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-insecure-public-base.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    await _seed_profile(settings)

    with pytest.raises(ConnectServiceError) as exc:
        await issue_connect_url(
            profile_id="default",
            session_id="desktop-session",
            base_url="http://chat.example.com",
            ttl_sec=120,
        )
    assert exc.value.error_code == "connect_base_url_insecure"

    with pytest.raises(ConnectServiceError) as single_label_exc:
        await issue_connect_url(
            profile_id="default",
            session_id="desktop-session",
            base_url="http://myhost",
            ttl_sec=120,
        )
    assert single_label_exc.value.error_code == "connect_base_url_insecure"


@pytest.mark.asyncio
async def test_connect_validate_access_rejects_unknown_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Access-token validation should fail for unknown token value."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-access-invalid.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)

    with pytest.raises(ConnectServiceError) as exc:
        await validate_connect_access_token(access_token="not-found")
    assert exc.value.error_code == "connect_access_token_invalid"


@pytest.mark.asyncio
async def test_connect_refresh_rejects_session_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Refresh token should stay bound to its original session id."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-refresh-session.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    await _seed_profile(settings)

    issued = await issue_connect_url(
        profile_id="default",
        session_id="desktop-session",
        base_url="http://127.0.0.1:8081",
        ttl_sec=120,
    )
    claim_token = parse_qs(urlparse(issued.connect_url).query)["claim_token"][0]
    claimed = await claim_connect_token(claim_token=claim_token)

    with pytest.raises(ConnectServiceError) as exc_info:
        await refresh_connect_access_token(
            refresh_token=claimed.refresh_token,
            session_proof=claimed.session_proof,
            session_id="desktop-session-2",
        )
    assert exc_info.value.error_code == "connect_session_override_forbidden"


@pytest.mark.asyncio
async def test_connect_issue_requires_existing_valid_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pairing should fail fast for missing or invalid profile ids."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-profile.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)

    with pytest.raises(ConnectServiceError) as missing_exc:
        await issue_connect_url(
            profile_id="missing",
            session_id="desktop-session",
            base_url="http://127.0.0.1:8081",
            ttl_sec=120,
        )
    assert missing_exc.value.error_code == "connect_profile_not_found"

    with pytest.raises(ConnectServiceError) as invalid_exc:
        await issue_connect_url(
            profile_id="bad/profile",
            session_id="desktop-session",
            base_url="http://127.0.0.1:8081",
            ttl_sec=120,
        )
    assert invalid_exc.value.error_code == "connect_profile_invalid"


@pytest.mark.asyncio
async def test_connect_validate_access_rejects_refresh_session_scope_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Access-token validation should fail closed when refresh-session scope drifts."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-access-mismatch.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    await _seed_profile(settings)

    issued = await issue_connect_url(
        profile_id="default",
        session_id="desktop-session",
        base_url="http://127.0.0.1:8081",
        ttl_sec=120,
    )
    claim_token = parse_qs(urlparse(issued.connect_url).query)["claim_token"][0]
    claimed = await claim_connect_token(claim_token=claim_token)

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        async with session_scope(factory) as db:
            await db.execute(
                update(ConnectSessionToken)
                .where(ConnectSessionToken.session_id == "desktop-session")
                .values(base_url="http://127.0.0.1:9090")
            )
    finally:
        await engine.dispose()

    with pytest.raises(ConnectServiceError) as exc:
        await validate_connect_access_token(
            access_token=claimed.access_token,
            session_proof=claimed.session_proof,
        )
    assert exc.value.error_code == "connect_access_token_invalid"


@pytest.mark.asyncio
async def test_connect_validate_access_rejects_refresh_session_profile_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Access-token validation should fail closed when refresh-session profile drifts."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-access-profile-mismatch.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    await _seed_profile(settings)

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        async with session_scope(factory) as db:
            await ProfileRepository(db).get_or_create_default("other")
    finally:
        await engine.dispose()

    issued = await issue_connect_url(
        profile_id="default",
        session_id="desktop-session",
        base_url="http://127.0.0.1:8081",
        ttl_sec=120,
    )
    claim_token = parse_qs(urlparse(issued.connect_url).query)["claim_token"][0]
    claimed = await claim_connect_token(claim_token=claim_token)

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        async with session_scope(factory) as db:
            await db.execute(
                update(ConnectSessionToken)
                .where(ConnectSessionToken.session_id == "desktop-session")
                .values(profile_id="other")
            )
    finally:
        await engine.dispose()

    with pytest.raises(ConnectServiceError) as exc:
        await validate_connect_access_token(
            access_token=claimed.access_token,
            session_proof=claimed.session_proof,
        )
    assert exc.value.error_code == "connect_access_token_invalid"


@pytest.mark.asyncio
async def test_connect_claim_can_require_pairing_pin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Claim PIN should protect one connect URL until caller proves out-of-band knowledge."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-claim-pin.db'}",
        root_dir=tmp_path,
        connect_claim_pin_max_attempts=3,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    await _seed_profile(settings)

    issued = await issue_connect_url(
        profile_id="default",
        session_id="desktop-session",
        base_url="http://127.0.0.1:8081",
        ttl_sec=120,
        claim_pin="2468",
    )
    claim_token = parse_qs(urlparse(issued.connect_url).query)["claim_token"][0]
    assert issued.claim_pin == "2468"

    with pytest.raises(ConnectServiceError) as missing_exc:
        await claim_connect_token(claim_token=claim_token)
    assert missing_exc.value.error_code == "connect_claim_pin_missing"

    with pytest.raises(ConnectServiceError) as invalid_exc:
        await claim_connect_token(claim_token=claim_token, claim_pin="1357")
    assert invalid_exc.value.error_code == "connect_claim_pin_invalid"

    claimed = await claim_connect_token(
        claim_token=claim_token,
        claim_pin="2468",
    )
    assert claimed.session_id == "desktop-session"


@pytest.mark.asyncio
async def test_connect_claim_pin_locks_after_too_many_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Too many wrong claim PIN attempts should permanently block the one-time token."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-claim-pin-lock.db'}",
        root_dir=tmp_path,
        connect_claim_pin_max_attempts=2,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    await _seed_profile(settings)

    issued = await issue_connect_url(
        profile_id="default",
        session_id="desktop-session",
        base_url="http://127.0.0.1:8081",
        ttl_sec=120,
        claim_pin="9999",
    )
    claim_token = parse_qs(urlparse(issued.connect_url).query)["claim_token"][0]

    with pytest.raises(ConnectServiceError) as first_exc:
        await claim_connect_token(claim_token=claim_token, claim_pin="1111")
    assert first_exc.value.error_code == "connect_claim_pin_invalid"

    with pytest.raises(ConnectServiceError) as second_exc:
        await claim_connect_token(claim_token=claim_token, claim_pin="2222")
    assert second_exc.value.error_code == "connect_claim_pin_locked"

    with pytest.raises(ConnectServiceError) as blocked_exc:
        await claim_connect_token(claim_token=claim_token, claim_pin="9999")
    assert blocked_exc.value.error_code == "connect_claim_pin_locked"


@pytest.mark.asyncio
async def test_connect_refresh_rejects_session_ids_longer_than_runtime_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Refresh should fail before DB writes when session id exceeds runtime limits."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-session-limit.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    await _seed_profile(settings)

    issued = await issue_connect_url(
        profile_id="default",
        session_id="desktop-session",
        base_url="http://127.0.0.1:8081",
        ttl_sec=120,
    )
    claim_token = parse_qs(urlparse(issued.connect_url).query)["claim_token"][0]
    claimed = await claim_connect_token(claim_token=claim_token)

    with pytest.raises(ConnectServiceError) as exc_info:
        await refresh_connect_access_token(
            refresh_token=claimed.refresh_token,
            session_proof=claimed.session_proof,
            session_id="x" * 65,
        )

    assert exc_info.value.error_code == "connect_session_invalid"


@pytest.mark.asyncio
async def test_connect_requires_session_proof_for_refresh_revoke_and_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """New connect sessions should require the session proof secret."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-proof.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    await _seed_profile(settings)

    issued = await issue_connect_url(
        profile_id="default",
        session_id="desktop-session",
        base_url="http://127.0.0.1:8081",
        ttl_sec=120,
    )
    claim_token = parse_qs(urlparse(issued.connect_url).query)["claim_token"][0]
    claimed = await claim_connect_token(claim_token=claim_token)

    with pytest.raises(ConnectServiceError) as access_exc:
        await validate_connect_access_token(access_token=claimed.access_token)
    assert access_exc.value.error_code == "connect_session_proof_missing"

    with pytest.raises(ConnectServiceError) as refresh_exc:
        await refresh_connect_access_token(refresh_token=claimed.refresh_token)
    assert refresh_exc.value.error_code == "connect_session_proof_missing"

    with pytest.raises(ConnectServiceError) as revoke_exc:
        await revoke_connect_session(refresh_token=claimed.refresh_token)
    assert revoke_exc.value.error_code == "connect_session_proof_missing"

    with pytest.raises(ConnectServiceError) as wrong_proof_exc:
        await validate_connect_access_token(
            access_token=claimed.access_token,
            session_proof="wrong-proof",
        )
    assert wrong_proof_exc.value.error_code == "connect_session_proof_invalid"


@pytest.mark.asyncio
async def test_connect_legacy_proofless_session_requires_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Legacy connect sessions without session proof must fail closed."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'connect-legacy-proofless.db'}",
        root_dir=tmp_path,
    )
    monkeypatch.setattr("afkbot.services.connect.service.get_settings", lambda: settings)
    await _seed_profile(settings)

    issued = await issue_connect_url(
        profile_id="default",
        session_id="desktop-session",
        base_url="http://127.0.0.1:8081",
        ttl_sec=120,
    )
    claim_token = parse_qs(urlparse(issued.connect_url).query)["claim_token"][0]
    claimed = await claim_connect_token(claim_token=claim_token)

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        async with session_scope(factory) as db:
            await db.execute(
                update(ConnectSessionToken)
                .values(session_proof_hash=None)
                .where(ConnectSessionToken.session_id == "desktop-session")
            )
    finally:
        await engine.dispose()

    with pytest.raises(ConnectServiceError) as access_exc:
        await validate_connect_access_token(
            access_token=claimed.access_token,
            session_proof=claimed.session_proof,
        )
    assert access_exc.value.error_code == "connect_session_reauth_required"

    with pytest.raises(ConnectServiceError) as refresh_exc:
        await refresh_connect_access_token(
            refresh_token=claimed.refresh_token,
            session_proof=claimed.session_proof,
        )
    assert refresh_exc.value.error_code == "connect_session_reauth_required"

    with pytest.raises(ConnectServiceError) as revoke_exc:
        await revoke_connect_session(
            refresh_token=claimed.refresh_token,
            session_proof=claimed.session_proof,
        )
    assert revoke_exc.value.error_code == "connect_session_reauth_required"
