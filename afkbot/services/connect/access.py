"""Access-token validation flow for desktop connect sessions."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.engine import create_engine
from afkbot.repositories.connect_repo import ConnectRepository
from afkbot.services.connect.context_snapshot import deserialize_runtime_metadata
from afkbot.services.connect.contracts import ConnectAccessTokenContext, ConnectServiceError
from afkbot.services.connect.helpers import as_utc, hash_token, validate_session_proof
from afkbot.services.connect.session_runtime import connect_session_scope
from afkbot.settings import Settings


async def validate_connect_access_token(
    *,
    settings: Settings,
    access_token: str,
    session_proof: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ConnectAccessTokenContext:
    """Validate connect access token and return bound profile/session scope."""

    token = access_token.strip()
    if not token:
        raise ConnectServiceError(
            error_code="connect_access_token_invalid",
            reason="Access token is empty.",
        )

    token_hash = hash_token(token)
    now = datetime.now(tz=UTC)
    async with connect_session_scope(
        session_factory=session_factory,
        settings=settings,
        engine_factory=create_engine,
    ) as db:
        repo = ConnectRepository(db)
        row = await repo.get_access_token_by_hash(access_token_hash=token_hash)
        if row is None:
            raise ConnectServiceError(
                error_code="connect_access_token_invalid",
                reason="Access token not found.",
            )
        if row.revoked_at is not None:
            raise ConnectServiceError(
                error_code="connect_access_token_revoked",
                reason="Access token is revoked.",
            )
        if as_utc(row.expires_at) <= now:
            raise ConnectServiceError(
                error_code="connect_access_token_expired",
                reason="Access token is expired.",
            )
        refresh_row = await repo.get_refresh_session_by_id(
            refresh_session_id=row.refresh_session_id,
        )
        if refresh_row is None:
            raise ConnectServiceError(
                error_code="connect_access_token_invalid",
                reason="Access token refresh session is missing.",
            )
        if (
            refresh_row.profile_id != row.profile_id
            or refresh_row.base_url != row.base_url
        ):
            raise ConnectServiceError(
                error_code="connect_access_token_invalid",
                reason="Access token scope does not match refresh session.",
            )
        if refresh_row.revoked_at is not None:
            raise ConnectServiceError(
                error_code="connect_access_token_revoked",
                reason="Access token refresh session is revoked.",
            )
        if not (refresh_row.session_proof_hash or "").strip():
            raise ConnectServiceError(
                error_code="connect_session_reauth_required",
                reason="Connect session must be paired again to enable proof-bound access.",
            )
        validate_session_proof(
            required_hash=refresh_row.session_proof_hash,
            proof_token=session_proof,
        )
        try:
            runtime_metadata = deserialize_runtime_metadata(row.runtime_metadata_json)
        except ValueError as exc:
            raise ConnectServiceError(
                error_code="connect_access_token_invalid",
                reason=str(exc),
            ) from exc
        return ConnectAccessTokenContext(
            profile_id=row.profile_id,
            session_id=row.session_id,
            base_url=row.base_url,
            expires_at=as_utc(row.expires_at),
            allow_diagnostics=bool(getattr(row, "allow_diagnostics", False)),
            runtime_metadata=runtime_metadata,
            prompt_overlay=(row.prompt_overlay or "").strip() or None,
        )
