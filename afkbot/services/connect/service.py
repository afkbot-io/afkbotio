"""Public desktop connect lifecycle facade."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.connect.access import validate_connect_access_token as _validate_connect_access_token
from afkbot.services.connect.contracts import (
    ConnectAccessTokenContext,
    ConnectClaimResult,
    ConnectClientMetadata,
    ConnectIssueResult,
    ConnectRefreshResult,
)
from afkbot.services.connect.issue import issue_connect_url as _issue_connect_url
from afkbot.services.connect.tokens import (
    claim_connect_token as _claim_connect_token,
    refresh_connect_access_token as _refresh_connect_access_token,
    revoke_connect_session as _revoke_connect_session,
)
from afkbot.settings import get_settings


async def issue_connect_url(
    *,
    profile_id: str,
    session_id: str,
    base_url: str,
    ttl_sec: int | None = None,
    allow_diagnostics: bool = False,
    claim_pin: str | None = None,
    context_overrides: TurnContextOverrides | None = None,
) -> ConnectIssueResult:
    """Issue one-time connect URL using current runtime settings."""

    return await _issue_connect_url(
        settings=get_settings(),
        profile_id=profile_id,
        session_id=session_id,
        base_url=base_url,
        ttl_sec=ttl_sec,
        allow_diagnostics=allow_diagnostics,
        claim_pin=claim_pin,
        context_overrides=context_overrides,
    )


async def claim_connect_token(
    *,
    claim_token: str,
    claim_pin: str | None = None,
    client: ConnectClientMetadata | None = None,
    access_ttl_sec: int = 3600,
    refresh_ttl_sec: int = 2_592_000,
) -> ConnectClaimResult:
    """Consume one-time claim token and issue session credentials."""

    return await _claim_connect_token(
        settings=get_settings(),
        claim_token=claim_token,
        claim_pin=claim_pin,
        client=client,
        access_ttl_sec=access_ttl_sec,
        refresh_ttl_sec=refresh_ttl_sec,
    )


async def refresh_connect_access_token(
    *,
    refresh_token: str,
    session_proof: str | None = None,
    session_id: str | None = None,
    access_ttl_sec: int = 3600,
) -> ConnectRefreshResult:
    """Issue a new access token for one refresh session."""

    return await _refresh_connect_access_token(
        settings=get_settings(),
        refresh_token=refresh_token,
        session_proof=session_proof,
        session_id=session_id,
        access_ttl_sec=access_ttl_sec,
    )


async def revoke_connect_session(*, refresh_token: str, session_proof: str | None = None) -> bool:
    """Revoke one refresh session and its access tokens."""

    return await _revoke_connect_session(
        settings=get_settings(),
        refresh_token=refresh_token,
        session_proof=session_proof,
    )


async def validate_connect_access_token(
    *,
    access_token: str,
    session_proof: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ConnectAccessTokenContext:
    """Validate one access token against current runtime settings."""

    return await _validate_connect_access_token(
        settings=get_settings(),
        access_token=access_token,
        session_proof=session_proof,
        session_factory=session_factory,
    )
