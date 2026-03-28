"""Issue flow for desktop connect URLs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import secrets

from afkbot.db.engine import create_engine
from afkbot.repositories.connect_repo import ConnectRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.sessions import SessionProfileMismatchError, SessionService
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.connect.context_snapshot import serialize_runtime_metadata, snapshot_from_turn_context
from afkbot.services.connect.contracts import ConnectIssueResult, ConnectServiceError
from afkbot.services.connect.helpers import (
    build_connect_url,
    hash_token,
    normalize_claim_pin,
    normalize_base_url,
    normalize_session_id,
    normalize_ttl,
)
from afkbot.services.connect.session_runtime import connect_session_scope
from afkbot.services.profile_id import InvalidProfileIdError, validate_profile_id
from afkbot.settings import Settings


async def issue_connect_url(
    *,
    settings: Settings,
    profile_id: str,
    session_id: str,
    base_url: str,
    ttl_sec: int | None = None,
    allow_diagnostics: bool = False,
    claim_pin: str | None = None,
    context_overrides: TurnContextOverrides | None = None,
) -> ConnectIssueResult:
    """Issue one-time connect URL and persist claim token hash."""

    normalized_ttl = normalize_ttl(ttl_sec)
    normalized_base_url = normalize_base_url(base_url)
    normalized_session_id = normalize_session_id(value=session_id, fallback="desktop-session")
    normalized_claim_pin = normalize_claim_pin(claim_pin)
    try:
        normalized_profile_id = validate_profile_id(profile_id.strip())
    except InvalidProfileIdError as exc:
        raise ConnectServiceError(
            error_code="connect_profile_invalid",
            reason=str(exc),
        ) from exc

    now = datetime.now(tz=UTC)
    expires_at = now + timedelta(seconds=normalized_ttl)
    claim_token = secrets.token_urlsafe(24)
    claim_token_hash = hash_token(claim_token)
    context_snapshot = snapshot_from_turn_context(context_overrides)

    async with connect_session_scope(
        settings=settings,
        engine_factory=create_engine,
    ) as db:
        profile = await ProfileRepository(db).get(normalized_profile_id)
        if profile is None:
            raise ConnectServiceError(
                error_code="connect_profile_not_found",
                reason=f"Profile not found: {normalized_profile_id}",
            )
        try:
            await SessionService(db).get_or_create(
                session_id=normalized_session_id,
                profile_id=normalized_profile_id,
            )
        except SessionProfileMismatchError as exc:
            raise ConnectServiceError(
                error_code="connect_session_profile_mismatch",
                reason=str(exc),
            ) from exc
        await ConnectRepository(db).create_claim_token(
            profile_id=normalized_profile_id,
            session_id=normalized_session_id,
            base_url=normalized_base_url,
            token_hash=claim_token_hash,
            claim_pin_hash=None if normalized_claim_pin is None else hash_token(normalized_claim_pin),
            allow_diagnostics=allow_diagnostics,
            runtime_metadata_json=serialize_runtime_metadata(
                None if context_snapshot is None else context_snapshot.runtime_metadata
            ),
            prompt_overlay=None if context_snapshot is None else context_snapshot.prompt_overlay,
            expires_at=expires_at,
        )

    connect_url = build_connect_url(
        base_url=normalized_base_url,
        claim_token=claim_token,
        profile_id=normalized_profile_id,
        session_id=normalized_session_id,
        expires_at=expires_at,
    )
    return ConnectIssueResult(
        connect_url=connect_url,
        expires_at=expires_at,
        profile_id=normalized_profile_id,
        session_id=normalized_session_id,
        base_url=normalized_base_url,
        claim_pin=normalized_claim_pin,
    )
